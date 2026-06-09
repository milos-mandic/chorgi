"""Tests for the knowledge layer against a throwaway SQLite database."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.knowledge import db as kdb
from agent.knowledge import models


class KnowledgeTestCase(unittest.TestCase):
    """Each test runs migrations into a fresh temp database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        # connect() reads these module constants at call time
        p1 = mock.patch.object(kdb, "PERSONAL_DIR", tmp_path)
        p2 = mock.patch.object(kdb, "DB_PATH", tmp_path / "knowledge.db")
        p1.start(); p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.addCleanup(self.tmp.cleanup)
        kdb.run_migrations()

    def test_migrations_apply_once(self):
        # Second run is a no-op
        self.assertEqual(kdb.run_migrations(), [])

    def test_person_roundtrip(self):
        p = models.upsert_person("Ada Lovelace", role="engineer", tags=["math"])
        self.assertEqual(p["name"], "Ada Lovelace")
        self.assertEqual(p["slug"], "ada-lovelace")
        got = models.get_person(p["id"])
        self.assertEqual(got["role"], "engineer")
        self.assertEqual([x["id"] for x in models.list_people()], [p["id"]])

    def test_match_person_by_name(self):
        p = models.upsert_person("Grace Hopper")
        self.assertEqual(models.match_person(name="grace hopper")["id"], p["id"])
        self.assertIsNone(models.match_person(name="nobody"))

    def test_inbox_reject(self):
        item = models.create_inbox_item(type="task", payload={"title": "x"})
        self.assertEqual(item["status"], "pending")
        self.assertEqual(len(models.list_inbox()), 1)
        self.assertTrue(models.reject_inbox_item(item["id"]))
        self.assertEqual(models.list_inbox(), [])
        # Already rejected → False
        self.assertFalse(models.reject_inbox_item(item["id"]))

    def test_topic_lifecycle(self):
        t = models.create_topic("Local LLMs", summary="running models locally")
        self.assertEqual(models.get_topic(t["id"])["title"], "Local LLMs")
        self.assertEqual(models.get_topic_by_slug(t["slug"])["id"], t["id"])
        self.assertTrue(
            models.assign_bookmark_to_topic(t["id"], "https://example.com/a"))
        topic = models.get_topic(t["id"])
        self.assertIn("https://example.com/a", topic["bookmark_urls"])
        self.assertTrue(models.delete_topic(t["id"]))
        self.assertIsNone(models.get_topic(t["id"]))


if __name__ == "__main__":
    unittest.main()
