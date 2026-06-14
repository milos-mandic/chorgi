"""Both context builders must lead with the current date/time line so the
router and sub-agents can resolve relative dates ("tomorrow", "3pm")."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.memory import Memory


class TestContextNowLine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_context_starts_with_now(self):
        self.assertTrue(self.mem.get_full_context().startswith("Current date/time:"))

    def test_haiku_context_starts_with_now(self):
        self.assertTrue(self.mem.get_haiku_context().startswith("Current date/time:"))


if __name__ == "__main__":
    unittest.main()
