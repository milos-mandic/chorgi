"""Tests for schedule trigger logic and save-time validation."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.scheduler import Scheduler, validate_schedule


def _utc(hour, minute=0):
    return datetime(2026, 6, 9, hour, minute, tzinfo=timezone.utc)


class TestIsDue(unittest.TestCase):
    def setUp(self):
        self.scheduler = Scheduler(orchestrator=None)

    def test_daily_before_hour_not_due(self):
        s = {"trigger": "daily", "at_hour": 8}
        self.assertFalse(self.scheduler._is_due(s, _utc(7)))

    def test_daily_after_hour_due(self):
        s = {"trigger": "daily", "at_hour": 8}
        self.assertTrue(self.scheduler._is_due(s, _utc(9)))

    def test_daily_already_ran_today_not_due(self):
        now = _utc(9)
        s = {"trigger": "daily", "at_hour": 8,
             "last_run": _utc(8, 5).isoformat()}
        self.assertFalse(self.scheduler._is_due(s, now))

    def test_daily_ran_yesterday_due(self):
        now = _utc(9)
        yesterday = (now - timedelta(days=1)).isoformat()
        s = {"trigger": "daily", "at_hour": 8, "last_run": yesterday}
        self.assertTrue(self.scheduler._is_due(s, now))

    def test_interval_never_ran_due(self):
        s = {"trigger": "interval", "interval_minutes": 30}
        self.assertTrue(self.scheduler._is_due(s, _utc(9)))

    def test_interval_elapsed_due(self):
        now = _utc(9)
        s = {"trigger": "interval", "interval_minutes": 30,
             "last_run": (now - timedelta(minutes=31)).isoformat()}
        self.assertTrue(self.scheduler._is_due(s, now))

    def test_interval_not_elapsed_not_due(self):
        now = _utc(9)
        s = {"trigger": "interval", "interval_minutes": 30,
             "last_run": (now - timedelta(minutes=10)).isoformat()}
        self.assertFalse(self.scheduler._is_due(s, now))

    def test_unknown_trigger_not_due(self):
        self.assertFalse(self.scheduler._is_due({"trigger": "weekly"}, _utc(9)))

    def test_string_at_hour_raises(self):
        # Documents why _check_schedules wraps _is_due per file: a malformed
        # schedule raises rather than silently misbehaving.
        s = {"trigger": "daily", "at_hour": "8"}
        with self.assertRaises(TypeError):
            self.scheduler._is_due(s, _utc(9))

    def test_recent_failed_attempt_not_due(self):
        # last_attempt survives a failed/interrupted run — back off 30 min.
        now = _utc(9)
        s = {"trigger": "daily", "at_hour": 8,
             "last_attempt": (now - timedelta(minutes=10)).isoformat()}
        self.assertFalse(self.scheduler._is_due(s, now))

    def test_old_failed_attempt_due_again(self):
        now = _utc(9)
        s = {"trigger": "daily", "at_hour": 8,
             "last_attempt": (now - timedelta(minutes=45)).isoformat()}
        self.assertTrue(self.scheduler._is_due(s, now))

    def test_recent_attempt_blocks_interval_too(self):
        now = _utc(9)
        s = {"trigger": "interval", "interval_minutes": 5,
             "last_attempt": (now - timedelta(minutes=6)).isoformat()}
        self.assertFalse(self.scheduler._is_due(s, now))


class TestMarkRan(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = Path(self.tmpdir.name) / "s.json"
        self.scheduler = Scheduler(orchestrator=None)

    def _write(self, data):
        import json
        self.path.write_text(json.dumps(data))

    def _read(self):
        import json
        return json.loads(self.path.read_text())

    def test_mark_ran_sets_last_run_and_clears_attempt(self):
        now = _utc(9)
        self._write({"name": "s", "trigger": "daily", "at_hour": 8,
                     "last_attempt": now.isoformat()})
        self.scheduler._mark_ran(self.path, now)
        data = self._read()
        self.assertEqual(data["last_run"], now.isoformat())
        self.assertNotIn("last_attempt", data)

    def test_mark_attempt_sets_last_attempt(self):
        now = _utc(9)
        self._write({"name": "s", "trigger": "daily", "at_hour": 8})
        self.scheduler._mark_attempt(self.path, now)
        self.assertEqual(self._read()["last_attempt"], now.isoformat())

    def test_mark_ran_leaves_no_tmp_file(self):
        now = _utc(9)
        self._write({"name": "s", "trigger": "daily", "at_hour": 8})
        self.scheduler._mark_ran(self.path, now)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()),
                         ["s.json"])


class TestValidateSchedule(unittest.TestCase):
    def _valid(self):
        return {"name": "x", "trigger": "daily", "at_hour": 8, "prompt": "do it"}

    def test_valid_daily(self):
        ok, err = validate_schedule(self._valid())
        self.assertTrue(ok, err)

    def test_coerces_string_at_hour(self):
        s = {**self._valid(), "at_hour": "8"}
        ok, _ = validate_schedule(s)
        self.assertTrue(ok)
        self.assertEqual(s["at_hour"], 8)
        self.assertIsInstance(s["at_hour"], int)

    def test_coerces_string_interval(self):
        s = {"name": "x", "trigger": "interval", "interval_minutes": "30", "prompt": "p"}
        ok, _ = validate_schedule(s)
        self.assertTrue(ok)
        self.assertEqual(s["interval_minutes"], 30)

    def test_rejects_missing_name(self):
        ok, err = validate_schedule({**self._valid(), "name": ""})
        self.assertFalse(ok)
        self.assertIn("name", err)

    def test_rejects_bad_trigger(self):
        ok, err = validate_schedule({**self._valid(), "trigger": "weekly"})
        self.assertFalse(ok)
        self.assertIn("trigger", err)

    def test_rejects_hour_out_of_range(self):
        ok, err = validate_schedule({**self._valid(), "at_hour": 24})
        self.assertFalse(ok)
        self.assertIn("at_hour", err)

    def test_rejects_unparseable_hour(self):
        ok, _ = validate_schedule({**self._valid(), "at_hour": "noonish"})
        self.assertFalse(ok)

    def test_rejects_zero_interval(self):
        ok, _ = validate_schedule(
            {"name": "x", "trigger": "interval", "interval_minutes": 0, "prompt": "p"})
        self.assertFalse(ok)

    def test_rejects_bad_type(self):
        ok, err = validate_schedule({**self._valid(), "type": "cron"})
        self.assertFalse(ok)
        self.assertIn("type", err)

    def test_rejects_missing_prompt(self):
        ok, err = validate_schedule({**self._valid(), "prompt": " "})
        self.assertFalse(ok)
        self.assertIn("prompt", err)

    def test_rejects_non_dict(self):
        ok, _ = validate_schedule("not a dict")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
