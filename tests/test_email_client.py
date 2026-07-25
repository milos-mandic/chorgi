"""Tests for email seen-UID tracking (no network)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "email"))

import email_client


class TestCheckNewEmails(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        seen_file = Path(self.tmpdir.name) / "email_seen.json"
        patcher = mock.patch.object(email_client, "SEEN_FILE", seen_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.seen_file = seen_file

    def _stub_unread(self, uids):
        return [{"uid": u, "subject": f"s{u}", "from": "a@b.c"} for u in uids]

    def test_new_emails_reported_once(self):
        with mock.patch.object(
            email_client, "fetch_unread",
            return_value=self._stub_unread(["1001", "1002"]),
        ):
            first = email_client.check_new_emails()
            second = email_client.check_new_emails()
        self.assertEqual([e["uid"] for e in first], ["1001", "1002"])
        self.assertEqual(second, [])
        self.assertEqual(set(json.loads(self.seen_file.read_text())),
                         {"1001", "1002"})

    def test_only_unseen_returned(self):
        self.seen_file.write_text(json.dumps(["1001"]))
        with mock.patch.object(
            email_client, "fetch_unread",
            return_value=self._stub_unread(["1001", "1003"]),
        ):
            new = email_client.check_new_emails()
        self.assertEqual([e["uid"] for e in new], ["1003"])

    def test_corrupt_seen_file_recovers(self):
        self.seen_file.write_text("{not json")
        with mock.patch.object(
            email_client, "fetch_unread",
            return_value=self._stub_unread(["1001"]),
        ):
            new = email_client.check_new_emails()
        self.assertEqual(len(new), 1)


if __name__ == "__main__":
    unittest.main()
