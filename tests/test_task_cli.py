"""Tests for task_cli: add/scheduling behavior and the batch scheduler.

The calendar is never touched for real — the immediate path is mocked at
task_cli._run_calendar_cli (the single subprocess choke point), and the batch
path runs against fake `calendar_client`/`scheduler` modules injected into
sys.modules (no google deps required in the test env).
"""

import argparse
import contextlib
import io
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
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


class TestParseScheduledAt(unittest.TestCase):
    def test_summer_is_cest(self):
        dt = task_cli._parse_scheduled_at("2026-06-20 15:00")
        self.assertEqual(dt.utcoffset().total_seconds(), 2 * 3600)  # +02:00

    def test_winter_is_cet(self):
        dt = task_cli._parse_scheduled_at("2026-01-20 15:00")
        self.assertEqual(dt.utcoffset().total_seconds(), 1 * 3600)  # +01:00


def _make_fakes(free_slots, created):
    """Build fake calendar_client + scheduler modules for the batch path."""
    cc = types.ModuleType("calendar_client")
    cc._get_calendar_ids = lambda: ("owner@x", "bot@x")
    cc.find_free_slots = lambda owner, bot, t0, t1, duration_minutes=30: free_slots

    def create_event(bot, title, start, end, description=None, attendees=None):
        eid = f"evt_{len(created)}"
        created.append({"title": title, "start": start, "end": end, "id": eid})
        return {"id": eid}

    cc.create_event = create_event

    sch = types.ModuleType("scheduler")
    sch.load_preferences = lambda: {"default_duration": 60, "buffer_minutes": 15}
    return cc, sch


class TestScheduleBatch(TaskCliBase):
    def _run_batch(self, free_slots):
        self.created = []
        cc, sch = _make_fakes(free_slots, self.created)
        sys.modules["calendar_client"] = cc
        sys.modules["scheduler"] = sch
        try:
            task_cli.cmd_schedule_batch(argparse.Namespace(days=3, dry_run=False))
        finally:
            sys.modules.pop("calendar_client", None)
            sys.modules.pop("scheduler", None)

    def test_batch_writes_back_link(self):
        task_cli.save_tasks([{
            "id": "t_1", "title": "Write blog", "notes": "", "priority": "medium",
            "estimated_minutes": 30, "deadline": None, "tags": [],
            "status": "pending", "created_at": "2026-06-01T00:00:00+00:00", "carry_count": 0,
        }])
        # A Saturday (weekday()==5) → fully available window.
        self._run_batch([{"start": "2026-06-20T08:00:00+00:00", "end": "2026-06-20T18:00:00+00:00"}])
        self.assertEqual(len(self.created), 1)
        t = task_cli.load_tasks()[0]
        self.assertEqual(t["status"], "scheduled")
        self.assertEqual(t["calendar_event_id"], "evt_0")
        self.assertIn("scheduled_at", t)

    def _pending(self, **kw):
        t = {
            "id": kw.get("id", "t_1"), "title": kw.get("title", "Do thing"), "notes": "",
            "priority": "medium", "estimated_minutes": kw.get("estimated_minutes", 30),
            "deadline": None, "tags": [], "status": "pending",
            "created_at": "2026-06-01T00:00:00+00:00", "carry_count": 0,
        }
        if "time_class" in kw:
            t["time_class"] = kw["time_class"]
        return t

    def test_batch_anytime_uses_weekday_daytime(self):
        task_cli.save_tasks([self._pending(time_class="anytime")])
        # Monday 07:00-23:00 CET; anytime allows from 08:00 CET.
        self._run_batch([{"start": "2026-06-22T05:00:00+00:00", "end": "2026-06-22T21:00:00+00:00"}])
        start_cet = self.created[0]["start"].astimezone(CET)
        self.assertEqual((start_cet.hour, start_cet.minute), (8, 0))

    def test_batch_off_hours_waits_for_evening(self):
        task_cli.save_tasks([self._pending(time_class="off_hours")])
        # Monday 07:00-23:00 CET; off_hours weekday starts at work_end 18:00 CET.
        self._run_batch([{"start": "2026-06-22T05:00:00+00:00", "end": "2026-06-22T21:00:00+00:00"}])
        start_cet = self.created[0]["start"].astimezone(CET)
        self.assertEqual((start_cet.hour, start_cet.minute), (18, 0))

    def test_batch_work_hours_uses_daytime(self):
        task_cli.save_tasks([self._pending(time_class="work_hours")])
        # Monday 07:00-23:00 CET; work_hours starts at work_start 09:00 CET.
        self._run_batch([{"start": "2026-06-22T05:00:00+00:00", "end": "2026-06-22T21:00:00+00:00"}])
        start_cet = self.created[0]["start"].astimezone(CET)
        self.assertEqual((start_cet.hour, start_cet.minute), (9, 0))

    def test_batch_off_hours_defers_when_only_workday_free(self):
        task_cli.save_tasks([self._pending(time_class="off_hours")])
        # Monday 10:00-16:00 CET only — entirely inside the work block.
        self._run_batch([{"start": "2026-06-22T08:00:00+00:00", "end": "2026-06-22T14:00:00+00:00"}])
        self.assertEqual(self.created, [])
        t = task_cli.load_tasks()[0]
        self.assertEqual(t["status"], "pending")
        self.assertEqual(t["carry_count"], 1)

    def test_batch_skips_already_linked_pending(self):
        task_cli.save_tasks([{
            "id": "t_linked", "title": "Already scheduled", "notes": "", "priority": "medium",
            "estimated_minutes": 30, "deadline": None, "tags": [], "status": "pending",
            "created_at": "2026-06-01T00:00:00+00:00", "carry_count": 0,
            "calendar_event_id": "old_evt", "scheduled_at": "2026-06-19T18:00:00+02:00",
        }, {
            "id": "t_clean", "title": "Fresh task", "notes": "", "priority": "medium",
            "estimated_minutes": 30, "deadline": None, "tags": [], "status": "pending",
            "created_at": "2026-06-01T00:00:00+00:00", "carry_count": 0,
        }])
        self._run_batch([{"start": "2026-06-20T08:00:00+00:00", "end": "2026-06-20T18:00:00+00:00"}])
        # Only the clean task gets an event; the linked one is untouched.
        self.assertEqual([c["title"] for c in self.created], ["Task: Fresh task"])
        linked = next(t for t in task_cli.load_tasks() if t["id"] == "t_linked")
        self.assertEqual(linked["calendar_event_id"], "old_evt")


class TestTimeClassRules(unittest.TestCase):
    MON, SAT = 0, 5  # weekday indices

    def test_class_bands(self):
        self.assertEqual(task_cli._class_bands(self.MON, "anytime", 9, 18), [(8, 22)])
        self.assertEqual(task_cli._class_bands(self.SAT, "anytime", 9, 18), [(8, 22)])
        self.assertEqual(task_cli._class_bands(self.MON, "work_hours", 9, 18), [(9, 18)])
        self.assertEqual(task_cli._class_bands(self.SAT, "work_hours", 9, 18), [])
        self.assertEqual(task_cli._class_bands(self.MON, "off_hours", 9, 18), [(18, 22)])
        self.assertEqual(task_cli._class_bands(self.SAT, "off_hours", 9, 18), [(8, 22)])

    def test_allowed_subranges_off_hours_excludes_workday(self):
        # Monday 09:00-17:00 CET (07:00-15:00 UTC), entirely in the work block.
        s = datetime(2026, 6, 22, 7, 0, tzinfo=timezone.utc)
        e = datetime(2026, 6, 22, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(task_cli._allowed_subranges(s, e, "off_hours", 9, 18), [])

    def test_allowed_subranges_off_hours_keeps_evening(self):
        # Monday 16:00-21:00 CET (14:00-19:00 UTC); off_hours keeps 18:00-21:00.
        s = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)
        e = datetime(2026, 6, 22, 19, 0, tzinfo=timezone.utc)
        out = task_cli._allowed_subranges(s, e, "off_hours", 9, 18)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0].astimezone(CET).hour, 18)


if __name__ == "__main__":
    unittest.main()
