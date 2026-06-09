"""Tests for skills/_shared.py — atomic saves and cross-process locking."""

import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills"))

import _shared


def _increment_counter(path_str):
    """Worker: read-modify-write a counter under the lock."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills"))
    import _shared as sh
    with sh.locked_json(path_str, {"count": 0}) as data:
        data["count"] += 1


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "data.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_missing_returns_default(self):
        self.assertEqual(_shared.load_json(self.path, []), [])
        self.assertIsNone(_shared.load_json(self.path))

    def test_save_then_load_roundtrip(self):
        _shared.save_json(self.path, {"a": [1, 2]})
        self.assertEqual(_shared.load_json(self.path), {"a": [1, 2]})

    def test_save_leaves_no_tmp_file(self):
        _shared.save_json(self.path, [1])
        leftovers = [p for p in self.path.parent.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_corrupt_raises_by_default(self):
        self.path.write_text("{not json")
        with self.assertRaises(json.JSONDecodeError):
            _shared.load_json(self.path, [])

    def test_corrupt_tolerant_returns_default(self):
        self.path.write_text("{not json")
        self.assertEqual(_shared.load_json(self.path, [], tolerant=True), [])

    def test_locked_json_writes_back(self):
        with _shared.locked_json(self.path, {"items": []}) as data:
            data["items"].append("x")
        self.assertEqual(_shared.load_json(self.path), {"items": ["x"]})


class TestFlockContention(unittest.TestCase):
    def test_concurrent_increments_lose_nothing(self):
        """N processes RMW one JSON counter; flock must serialize them."""
        n = 12
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "counter.json")
            ctx = multiprocessing.get_context("spawn")
            with ctx.Pool(6) as pool:
                pool.map(_increment_counter, [path] * n)
            self.assertEqual(_shared.load_json(path)["count"], n)


if __name__ == "__main__":
    unittest.main()
