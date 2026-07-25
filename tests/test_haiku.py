"""Tests for the Haiku response-parsing pipeline (no network)."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import haiku


def _classify(raw: str) -> dict:
    async def fake_call_haiku(**kwargs):
        return raw, {"input_tokens": 1, "output_tokens": 1}

    with mock.patch.object(haiku, "call_haiku", new=fake_call_haiku):
        return asyncio.run(
            haiku.classify_and_respond("hi", [], "ctx", "router"))


class TestClassifyParsing(unittest.TestCase):
    def test_clean_json(self):
        result = _classify('{"route": "haiku", "response": "hey"}')
        self.assertEqual(result["route"], "haiku")
        self.assertEqual(result["response"], "hey")

    def test_fenced_json(self):
        result = _classify('```json\n{"route": "sub_agent", "skill": "email"}\n```')
        self.assertEqual(result["route"], "sub_agent")
        self.assertEqual(result["skill"], "email")

    def test_embedded_json(self):
        result = _classify('Sure, here you go: {"route": "schedule"} hope that helps')
        self.assertEqual(result["route"], "schedule")

    def test_plain_text_falls_through_as_response(self):
        result = _classify("Just a friendly plain answer.")
        self.assertEqual(result["route"], "haiku")
        self.assertEqual(result["response"], "Just a friendly plain answer.")

    def test_broken_json_not_shown_to_user(self):
        # Half-emitted JSON must not become the user-visible reply.
        result = _classify('{"route": "haiku", "resp')
        self.assertEqual(result["route"], "haiku")
        self.assertNotIn("{", result["response"])

    def test_usage_attached(self):
        result = _classify('{"route": "haiku", "response": "x"}')
        self.assertIn("_usage", result)


if __name__ == "__main__":
    unittest.main()
