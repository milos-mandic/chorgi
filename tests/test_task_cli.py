"""Tests for task_cli: add/scheduling/remove behavior.

The calendar is never touched for real — the calendar path is mocked at
task_cli._run_calendar_cli (the single subprocess choke point), so no google
deps are required in the test env.
"""

import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "tasks"))

import task_cli  # noqa: E402

CET = ZoneInfo("Europe/Berlin")


def _add_args(title, **kw):
    ns = argparse.Namespace(
        title=title, notes=kw.get("notes", ""), priority=kw.get("priority"),
        estimate=kw.get("estimate"), deadline=kw.get("deadline"),
        tags=kw.get("tags", ""), scheduled_at=kw.get("scheduled_at"),
    )
    return ns


class TaskCliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data = task_cli.DATA_FILE
        task_cli.DATA_FILE = Path(self._tmp.name) / "tasks.json"
        task_cli.save_tasks([])
        self._orig_run = task_cli._run_calendar_cli
        # The cmd_* functions print results to stdout; keep test output clean.
        self._stdout = contextlib.redirect_stdout(io.StringIO())
        self._stdout.__enter__()

    def tearDown(self):
        self._stdout.__exit__(None, None, None)
        task_cli.DATA_FILE = self._orig_data
        task_cli._run_calendar_cli = self._orig_run
        self._tmp.cleanup()


class TestAdd(TaskCliBase):
    def test_add_plain_is_pending_unlinked(self):
        task_cli.cmd_add(_add_args("Buy milk"))
        tasks = task_cli.load_tasks()
        self.assertEqual(len(tasks), 1)
        t = tasks[0]
        self.assertEqual(t["status"], "pending")
        self.assertNotIn("calendar_event_id", t)
        self.assertNotIn("scheduled_at", t)

    def test_add_deadline_only_does_not_touch_calendar(self):
        called = []
        task_cli._run_calendar_cli = lambda a: called.append(a) or {"ok": True, "data": {"id": "x"}}
        task_cli.cmd_add(_add_args("File taxes", deadline="2026-07-01"))
        self.assertEqual(called, [])  # no calendar call for a deadline-only task
        t = task_cli.load_tasks()[0]
        self.assertEqual(t["status"], "pending")
        self.assertEqual(t["deadline"], "2026-07-01")

    def test_add_scheduled_success_links_and_prefixes_title(self):
        calls = []

        def fake_run(a):
            calls.append(a)
            return {"ok": True, "data": {"id": "evt123"}}

        task_cli._run_calendar_cli = fake_run
        task_cli.cmd_add(_add_args("Call dentist", scheduled_at="2026-06-20 15:00", estimate=30))
        t = task_cli.load_tasks()[0]
        self.assertEqual(t["status"], "scheduled")
        self.assertEqual(t["calendar_event_id"], "evt123")
        self.assertIn("scheduled_at", t)
        # Exactly one calendar call, titled with the unified "Task:" prefix.
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertEqual(argv[0], "create")
        self.assertEqual(argv[1], "Task: Call dentist")

    def test_add_scheduled_failure_stays_pending_with_requested_at(self):
        task_cli._run_calendar_cli = lambda a: {"ok": False, "error": "boom"}
        task_cli.cmd_add(_add_args("Call dentist", scheduled_at="2026-06-20 15:00"))
        t = task_cli.load_tasks()[0]
        self.assertEqual(t["status"], "pending")
        self.assertNotIn("calendar_event_id", t)
        self.assertEqual(t["requested_at"], "2026-06-20 15:00")


class TestRemove(TaskCliBase):
    def _linked_task(self):
        return {
            "id": "t_1", "title": "Coffee with Maria", "notes": "", "priority": "medium",
            "estimated_minutes": 60, "deadline": None, "tags": [], "status": "scheduled",
            "created_at": "2026-06-01T00:00:00+00:00", "carry_count": 0,
            "calendar_event_id": "evt_abc", "scheduled_at": "2026-06-20T10:00:00+02:00",
        }

    def test_remove_deletes_linked_calendar_event(self):
        calls = []
        task_cli._run_calendar_cli = lambda a: calls.append(a) or {"ok": True, "data": {}}
        task_cli.save_tasks([self._linked_task()])
        task_cli.cmd_remove(argparse.Namespace(task_id="t_1"))
        self.assertEqual(task_cli.load_tasks(), [])
        # The calendar event tied to the task was deleted.
        self.assertEqual(calls, [["delete", "evt_abc"]])

    def test_remove_unlinked_task_makes_no_calendar_call(self):
        calls = []
        task_cli._run_calendar_cli = lambda a: calls.append(a) or {"ok": True, "data": {}}
        t = self._linked_task()
        del t["calendar_event_id"]
        t["status"] = "pending"
        task_cli.save_tasks([t])
        task_cli.cmd_remove(argparse.Namespace(task_id="t_1"))
        self.assertEqual(task_cli.load_tasks(), [])
        self.assertEqual(calls, [])

    def test_remove_keeps_task_data_consistent_when_calendar_delete_fails(self):
        # Even if the calendar call fails, the task is still removed locally.
        task_cli._run_calendar_cli = lambda a: {"ok": False, "error": "gone"}
        task_cli.save_tasks([self._linked_task()])
        task_cli.cmd_remove(argparse.Namespace(task_id="t_1"))
        self.assertEqual(task_cli.load_tasks(), [])


class TestParseScheduledAt(unittest.TestCase):
    def test_summer_is_cest(self):
        dt = task_cli._parse_scheduled_at("2026-06-20 15:00")
        self.assertEqual(dt.utcoffset().total_seconds(), 2 * 3600)  # +02:00

    def test_winter_is_cet(self):
        dt = task_cli._parse_scheduled_at("2026-01-20 15:00")
        self.assertEqual(dt.utcoffset().total_seconds(), 1 * 3600)  # +01:00


if __name__ == "__main__":
    unittest.main()
