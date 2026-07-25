"""Tests for the nightly backup job."""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.backup import run_backup, KEEP_DAYS


def _make_tree(base: Path):
    (base / ".personal" / "memory").mkdir(parents=True)
    (base / ".personal" / "memory" / "short_term.md").write_text("note\n")
    (base / "schedules").mkdir()
    (base / "schedules" / "s.json").write_text('{"name": "s"}\n')
    (base / "skills" / "tasks" / "workspace").mkdir(parents=True)
    (base / "skills" / "tasks" / "workspace" / "tasks.json").write_text("[]\n")

    db = sqlite3.connect(str(base / ".personal" / "knowledge.db"))
    db.execute("CREATE TABLE t (x INTEGER)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()
    db.close()


class TestRunBackup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.base = Path(self.tmpdir.name)
        _make_tree(self.base)
        self.now = datetime(2026, 7, 25, 3, 0, tzinfo=timezone.utc)

    def test_backs_up_all_state(self):
        summary = run_backup(base_dir=self.base, now=self.now)
        dest = self.base / ".personal" / "backups" / "2026-07-25"

        db = sqlite3.connect(str(dest / "knowledge.db"))
        self.assertEqual(db.execute("SELECT x FROM t").fetchone()[0], 42)
        db.close()

        self.assertEqual(
            json.loads((dest / "schedules" / "s.json").read_text())["name"], "s")
        self.assertTrue((dest / "memory" / "short_term.md").exists())
        self.assertTrue(
            (dest / "workspaces" / "tasks" / "tasks.json").exists())
        self.assertIn("knowledge.db", summary)

    def test_prunes_old_backups(self):
        root = self.base / ".personal" / "backups"
        for day in range(1, KEEP_DAYS + 3):
            (root / f"2026-07-{day:02d}").mkdir(parents=True)
        run_backup(base_dir=self.base, now=self.now)
        remaining = sorted(d.name for d in root.iterdir())
        self.assertEqual(len(remaining), KEEP_DAYS)
        self.assertNotIn("2026-07-01", remaining)
        self.assertIn("2026-07-25", remaining)

    def test_rerun_same_day_overwrites_cleanly(self):
        run_backup(base_dir=self.base, now=self.now)
        summary = run_backup(base_dir=self.base, now=self.now)
        self.assertIn("2026-07-25", summary)


if __name__ == "__main__":
    unittest.main()
