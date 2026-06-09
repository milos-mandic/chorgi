"""Tests for the linkedin_cli argparse surface (no data files touched)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "linkedin"))

from linkedin_cli import build_parser


class TestParser(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def _parse(self, argv):
        return self.parser.parse_args(argv)

    def test_calendar_commands(self):
        self.assertEqual(self._parse(["calendar", "show"]).command, "show")
        self.assertEqual(self._parse(["calendar", "context"]).command, "context")
        args = self._parse(["calendar", "set", '{"week_of": "2026-06-08"}'])
        self.assertEqual(args.json_str, '{"week_of": "2026-06-08"}')
        args = self._parse(["calendar", "get", "today"])
        self.assertEqual(args.date_or_day, "today")

    def test_calendar_update_flag_and_positional(self):
        args = self._parse(["calendar", "update", "2026-06-09", "--status", "drafted"])
        self.assertEqual((args.date, args.status), ("2026-06-09", "drafted"))
        args = self._parse(["calendar", "update", "2026-06-09", "posted"])
        self.assertEqual(args.status_pos, "posted")

    def test_calendar_update_missing_status_parses(self):
        # Previously crashed with IndexError; now parses and the dispatcher
        # rejects it with a usage error instead.
        args = self._parse(["calendar", "update", "2026-06-09"])
        self.assertIsNone(args.status)
        self.assertIsNone(args.status_pos)

    def test_feed_commands(self):
        args = self._parse(["feed", "list", "--pillar", "technical_craft", "--unused"])
        self.assertEqual(args.pillar, "technical_craft")
        self.assertTrue(args.unused)
        self.assertEqual(self._parse(["feed", "use", "3"]).item_id, 3)
        self.assertEqual(self._parse(["feed", "remove", "4"]).item_id, 4)

    def test_feed_use_non_integer_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse(["feed", "use", "abc"])

    def test_history_and_viral_defaults(self):
        self.assertEqual(self._parse(["history", "show"]).weeks, 4)
        self.assertEqual(self._parse(["history", "show", "--weeks", "8"]).weeks, 8)
        self.assertEqual(self._parse(["viral", "show"]).last, 10)
        self.assertEqual(self._parse(["pillars", "rotation"]).command, "rotation")

    def test_unknown_group_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse(["nonsense", "show"])

    def test_missing_command_rejected(self):
        with self.assertRaises(SystemExit):
            self._parse(["calendar"])


if __name__ == "__main__":
    unittest.main()
