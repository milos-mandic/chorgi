"""Tests for the knowledge DB connection hardening + migration runner."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.knowledge import db


class TestConnect(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        tmp = Path(self.tmpdir.name)
        self._patches = [
            mock.patch.object(db, "PERSONAL_DIR", tmp),
            mock.patch.object(db, "DB_PATH", tmp / "knowledge.db"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_pragmas(self):
        conn = db.connect()
        try:
            self.assertEqual(
                conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(
                conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            self.assertEqual(
                conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            # synchronous NORMAL == 1
            self.assertEqual(
                conn.execute("PRAGMA synchronous").fetchone()[0], 1)
        finally:
            conn.close()

    def test_migrations_idempotent(self):
        first = db.run_migrations()
        self.assertTrue(first)  # fresh DB applies every schema file
        second = db.run_migrations()
        self.assertEqual(second, [])  # re-run is a no-op


if __name__ == "__main__":
    unittest.main()
