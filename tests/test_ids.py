"""Tests for the ULID generator."""

import concurrent.futures
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.knowledge.ids import CROCKFORD, ulid


class TestUlid(unittest.TestCase):
    def test_length_and_alphabet(self):
        u = ulid()
        self.assertEqual(len(u), 26)
        self.assertTrue(all(c in CROCKFORD for c in u))

    def test_monotonic_in_tight_loop(self):
        ids = [ulid() for _ in range(2000)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_unique_across_threads(self):
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            ids = list(ex.map(lambda _: ulid(), range(4000)))
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
