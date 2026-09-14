"""Tests for api_handlers._update_task — the dashboard's PATCH /api/tasks/<id>.

The calendar is never touched for real: task_cli._run_calendar_cli (the single
subprocess choke point) is replaced. For the sync_calendar=False path it is
replaced with a raiser, so any accidental calendar call fails the test loudly.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "skills"))
sys.path.insert(0, str(BASE / "skills" / "tasks"))

from agent import api_handlers  # noqa: E402
import task_cli  # noqa: E402

CET = ZoneInfo("Europe/Berlin")


def _boom(args):
    raise AssertionError(f"calendar_cli must not be called, got: {args}")


class UpdateTaskBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_data = task_cli.DATA_FILE
        task_cli.DATA_FILE = Path(self._tmp.name) / "tasks.json"
        task_cli.save_tasks([])
        self._orig_run = task_cli._run_calendar_cli
        api_handlers._task_cli = task_cli  # skip the lazy import

    def tearDown(self):
        task_cli._run_calendar_cli = self._orig_run
        task_cli.DATA_FILE = self._orig_data
        api_handlers._task_cli = None
        self._tmp.cleanup()

    def seed(self, **fields):
        task = {
            "id": "t_test_1", "title": "Edit podcast", "notes": "",
            "priority": "medium", "estimated_minutes": 30, "deadline": None,
            "tags": [], "status": "pending",
            "created_at": "2026-07-25T21:16:02.605156+00:00", "carry_count": 0,
        }
        task.update(fields)
        task_cli.save_tasks([task])
        return task

    def seed_many(self, *ids, **fields):
        tasks = []
        for tid in ids:
            task = {
                "id": tid, "title": tid, "notes": "", "priority": "medium",
                "estimated_minutes": 30, "deadline": None, "tags": [],
                "status": "pending",
                "created_at": "2026-07-25T21:16:02.605156+00:00", "carry_count": 0,
            }
            task.update(fields)
            tasks.append(task)
        task_cli.save_tasks(tasks)
        return tasks

    def stored(self):
        return {t["id"]: t for t in task_cli.load_tasks()}


class TestBoardDragDoesNotTouchCalendar(UpdateTaskBase):
    """sync_calendar=False — what the week-board drag sends."""

    def test_planning_an_undated_task_makes_no_calendar_call(self):
        self.seed()
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-30 09:00", "sync_calendar": False})
        self.assertEqual(out["scheduled_at"], "2026-07-30T09:00:00+02:00")
        self.assertNotIn("calendar_event_id", out)
        # status is untouched: "scheduled" has always meant "has a calendar event"
        self.assertEqual(out["status"], "pending")

    def test_moving_a_calendar_backed_task_leaves_the_event_alone(self):
        self.seed(status="scheduled", scheduled_at="2026-07-26T11:00:00+02:00",
                  calendar_event_id="ev_abc")
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-30 11:00", "sync_calendar": False})
        self.assertEqual(out["scheduled_at"], "2026-07-30T11:00:00+02:00")
        self.assertEqual(out["calendar_event_id"], "ev_abc")  # link kept, event unmoved

    def test_drop_on_pending_clears_date_but_keeps_the_event_link(self):
        self.seed(status="scheduled", scheduled_at="2026-07-26T11:00:00+02:00",
                  calendar_event_id="ev_abc")
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": None, "sync_calendar": False})
        self.assertNotIn("scheduled_at", out)
        self.assertEqual(out["status"], "pending")
        # Kept so a later "When" edit moves the real event instead of orphaning it
        self.assertEqual(out["calendar_event_id"], "ev_abc")

    def test_malformed_date_is_ignored_rather_than_stored(self):
        self.seed()
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "next tuesday", "sync_calendar": False})
        self.assertNotIn("scheduled_at", out)

    def test_other_fields_still_apply(self):
        self.seed()
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-30 09:00", "sync_calendar": False,
                         "tags": "podcast, fde-hub"})
        self.assertEqual(out["tags"], ["podcast", "fde-hub"])


class TestManualOrdering(UpdateTaskBase):
    """`order` — the destination column's full id list, sent on every board drop."""

    def test_order_assigns_contiguous_indexes(self):
        self.seed_many("t_a", "t_b", "t_c")
        task_cli._run_calendar_cli = _boom
        api_handlers._update_task(
            "t_b", {"order": ["t_c", "t_b", "t_a"], "sync_calendar": False})
        s = self.stored()
        self.assertEqual([s["t_c"]["sort_order"], s["t_b"]["sort_order"],
                          s["t_a"]["sort_order"]], [0, 1, 2])

    def test_reordering_needs_no_date_change(self):
        """Dragging within one day sends order and no scheduled_at."""
        self.seed_many("t_a", "t_b", scheduled_at="2026-07-30T09:00:00+02:00",
                       status="scheduled", calendar_event_id="ev_x")
        task_cli._run_calendar_cli = _boom
        api_handlers._update_task("t_b", {"order": ["t_b", "t_a"], "sync_calendar": False})
        s = self.stored()
        self.assertEqual(s["t_b"]["sort_order"], 0)
        # untouched: a within-column drag is not a reschedule
        self.assertEqual(s["t_b"]["scheduled_at"], "2026-07-30T09:00:00+02:00")
        self.assertEqual(s["t_b"]["status"], "scheduled")

    def test_moving_day_and_position_is_one_write(self):
        self.seed_many("t_a", "t_b")
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task("t_b", {
            "scheduled_at": "2026-07-31 09:00",
            "order": ["t_b", "t_a"],
            "sync_calendar": False,
        })
        self.assertEqual(out["scheduled_at"], "2026-07-31T09:00:00+02:00")
        self.assertEqual(out["sort_order"], 0)

    def test_unknown_ids_are_skipped(self):
        """A card deleted in another tab mid-drag must not break the write."""
        self.seed_many("t_a")
        task_cli._run_calendar_cli = _boom
        api_handlers._update_task(
            "t_a", {"order": ["t_gone", "t_a"], "sync_calendar": False})
        self.assertEqual(self.stored()["t_a"]["sort_order"], 1)

    def test_order_matches_ids_exactly_not_by_prefix(self):
        """find_task() prefix-matches; _apply_order must not, or it reorders the wrong card."""
        self.seed_many("t_a", "t_a_longer")
        task_cli._run_calendar_cli = _boom
        api_handlers._update_task("t_a", {"order": ["t_a"], "sync_calendar": False})
        s = self.stored()
        self.assertEqual(s["t_a"]["sort_order"], 0)
        self.assertNotIn("sort_order", s["t_a_longer"])

    def test_sort_order_is_not_settable_directly(self):
        self.seed()
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task("t_test_1", {"sort_order": 99, "sync_calendar": False})
        self.assertNotIn("sort_order", out)

    def test_changing_day_elsewhere_drops_the_stale_index(self):
        """Rescheduled from the modal, not the board: its index was the old column's."""
        self.seed(sort_order=3)
        task_cli._run_calendar_cli = lambda a: {"ok": True, "data": {"id": "ev_new"}}
        out = api_handlers._update_task("t_test_1", {"scheduled_at": "2026-07-30 09:00"})
        self.assertNotIn("sort_order", out)

    def test_editing_without_moving_keeps_the_index(self):
        """The modal resends scheduled_at unchanged on every save."""
        self.seed(sort_order=3, status="scheduled",
                  scheduled_at="2026-07-30T09:00:00+02:00", calendar_event_id="ev_abc")
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"title": "Renamed", "scheduled_at": "2026-07-30 09:00",
                         "sync_calendar": False})
        self.assertEqual(out["sort_order"], 3)

    def test_completing_a_task_keeps_its_index(self):
        """Done cards are pinned to the bottom by the client, not by clearing order."""
        self.seed(sort_order=2)
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task("t_test_1", {"status": "done"})
        self.assertEqual(out["sort_order"], 2)
        self.assertIn("completed_at", out)


class TestCalendarToggle(UpdateTaskBase):
    """The task editor: only `calendar: true` puts a task on the calendar."""

    def recorder(self, data=None):
        calls = []

        def fake(args):
            calls.append(args)
            return {"ok": True, "data": data if data is not None else {"id": "ev_new"}}

        task_cli._run_calendar_cli = fake
        return calls

    def test_setting_when_without_toggle_creates_no_event(self):
        self.seed()
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-30 09:00", "calendar": False})
        self.assertEqual(out["scheduled_at"], "2026-07-30T09:00:00+02:00")
        self.assertNotIn("calendar_event_id", out)
        self.assertEqual(out["status"], "pending")

    def test_saving_a_dragged_task_does_not_add_it_to_calendar(self):
        """The old leak: modal resends the time in a different format than stored."""
        self.seed(scheduled_at="2026-07-30T09:00:00+02:00", status="pending")
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"title": "Renamed", "scheduled_at": "2026-07-30 09:00"})
        self.assertEqual(out["title"], "Renamed")
        self.assertNotIn("calendar_event_id", out)

    def test_toggle_on_creates_an_event(self):
        self.seed()
        calls = self.recorder()
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-30 09:00", "calendar": True})
        self.assertEqual(out["calendar_event_id"], "ev_new")
        self.assertEqual(out["calendar_at"], "2026-07-30T09:00:00+02:00")
        self.assertEqual(out["status"], "scheduled")
        self.assertEqual(calls[0][0], "create")

    def test_toggle_off_deletes_the_event_and_keeps_the_date(self):
        self.seed(status="scheduled", scheduled_at="2026-07-26T11:00:00+02:00",
                  calendar_event_id="ev_abc", calendar_at="2026-07-26T11:00:00+02:00")
        calls = self.recorder({})
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-26 11:00", "calendar": False})
        self.assertEqual(calls[0][0], "delete")
        self.assertNotIn("calendar_event_id", out)
        self.assertNotIn("calendar_at", out)
        self.assertEqual(out["scheduled_at"], "2026-07-26T11:00:00+02:00")
        self.assertEqual(out["status"], "pending")

    def test_resaving_unchanged_time_makes_no_calendar_call(self):
        self.seed(status="scheduled", scheduled_at="2026-07-26T11:00:00+02:00",
                  calendar_event_id="ev_abc", calendar_at="2026-07-26T11:00:00+02:00")
        task_cli._run_calendar_cli = _boom
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-26 11:00", "calendar": True})
        self.assertEqual(out["calendar_event_id"], "ev_abc")

    def test_toggle_on_moves_an_event_a_drag_left_behind(self):
        self.seed(status="scheduled", scheduled_at="2026-07-30T11:00:00+02:00",
                  calendar_event_id="ev_abc", calendar_at="2026-07-26T11:00:00+02:00")
        calls = self.recorder({"id": "ev_abc"})
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-30 11:00", "calendar": True})
        self.assertEqual(calls[0][0], "update")
        self.assertEqual(out["calendar_at"], "2026-07-30T11:00:00+02:00")

    def test_calendar_failure_is_reported_not_stored(self):
        self.seed()
        task_cli._run_calendar_cli = lambda a: {"ok": False, "error": "no auth"}
        out = api_handlers._update_task(
            "t_test_1", {"scheduled_at": "2026-07-30 09:00", "calendar": True})
        self.assertEqual(out["_calendar_warning"], "no auth")
        self.assertEqual(out["status"], "pending")
        self.assertNotIn("_calendar_warning", self.stored()["t_test_1"])
        self.assertEqual(self.stored()["t_test_1"]["scheduled_at"], "2026-07-30T09:00:00+02:00")

    def test_create_with_time_but_no_toggle_stays_off_calendar(self):
        task_cli._run_calendar_cli = _boom
        out = api_handlers._create_task({"title": "Plan", "scheduled_at": "2026-07-30 09:00"})
        self.assertEqual(out["scheduled_at"], "2026-07-30T09:00:00+02:00")
        self.assertNotIn("calendar_event_id", out)

    def test_create_with_toggle_adds_to_calendar(self):
        calls = self.recorder()
        out = api_handlers._create_task(
            {"title": "Call", "scheduled_at": "2026-07-30 09:00", "calendar": True})
        self.assertEqual(calls[0][0], "create")
        self.assertEqual(out["calendar_event_id"], "ev_new")
        self.assertEqual(out["status"], "scheduled")

    def test_clearing_when_deletes_the_event(self):
        self.seed(status="scheduled", scheduled_at="2026-07-26T11:00:00+02:00",
                  calendar_event_id="ev_abc")
        calls = []

        def fake(args):
            calls.append(args)
            return {"ok": True, "data": {}}

        task_cli._run_calendar_cli = fake
        out = api_handlers._update_task("t_test_1", {"scheduled_at": None})
        self.assertNotIn("calendar_event_id", out)
        self.assertEqual(out["status"], "pending")
        self.assertEqual(calls[0][0], "delete")


class TestNormalizeScheduledAt(unittest.TestCase):
    def test_summer_is_cest(self):
        self.assertEqual(task_cli.normalize_scheduled_at("2026-07-30 09:00"),
                         "2026-07-30T09:00:00+02:00")

    def test_winter_is_cet(self):
        self.assertEqual(task_cli.normalize_scheduled_at("2026-01-30 09:00"),
                         "2026-01-30T09:00:00+01:00")

    def test_tolerates_iso_t_separator(self):
        self.assertEqual(task_cli.normalize_scheduled_at("2026-07-30T09:00"),
                         "2026-07-30T09:00:00+02:00")


if __name__ == "__main__":
    unittest.main()
