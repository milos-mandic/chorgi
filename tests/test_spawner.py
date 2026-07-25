"""Tests for sub-agent spawning via a stub claude binary (no network)."""

import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import spawner


class TestSpawnSubAgent(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.skill_dir = Path(self.tmpdir.name)

    def _stub(self, script_body: str) -> str:
        stub = self.skill_dir / "claude_stub.sh"
        stub.write_text(f"#!/bin/sh\n{script_body}\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return str(stub)

    def _run(self, script_body: str, timeout_seconds: int = 10) -> dict:
        config = {
            "dir": str(self.skill_dir),
            "name": "stub",
            "tools": [],
            "max_turns": 1,
            "timeout_seconds": timeout_seconds,
        }
        with mock.patch.object(spawner, "CLAUDE_BIN", self._stub(script_body)):
            return asyncio.run(spawner.spawn_sub_agent(config, "task", "ctx"))

    def test_json_result(self):
        result = self._run('echo \'{"result": "hello"}\'')
        self.assertEqual(result["text"], "hello")
        self.assertNotIn("error", result)

    def test_non_json_output_returned_raw(self):
        result = self._run("echo plain text output")
        self.assertEqual(result["text"], "plain text output")

    def test_nonzero_exit_is_error(self):
        result = self._run('echo "boom" >&2; exit 1')
        self.assertTrue(result.get("error"))
        self.assertIn("boom", result["message"])

    def test_timeout_kills_process(self):
        result = self._run("exec sleep 30", timeout_seconds=1)
        self.assertTrue(result.get("error"))
        self.assertIn("timed out", result["message"])

    def test_no_live_processes_left_behind(self):
        self._run('echo \'{"result": "ok"}\'')
        self._run("exec sleep 30", timeout_seconds=1)
        self.assertFalse(
            [p for p in spawner._live_processes if p.returncode is None])


if __name__ == "__main__":
    unittest.main()
