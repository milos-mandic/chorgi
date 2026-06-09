"""Tests for the Anthropic API client retry/backoff behavior (stubbed urlopen)."""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import api_client


def _ok_response():
    body = json.dumps({
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }).encode()

    class _Resp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp()


def _http_error(code, headers=None):
    return urllib.error.HTTPError(
        url=api_client.API_URL, code=code, msg="err",
        hdrs=headers or {}, fp=io.BytesIO(b"{}"),
    )


class TestRetry(unittest.TestCase):
    def setUp(self):
        patcher_key = mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
        patcher_key.start()
        self.addCleanup(patcher_key.stop)
        # Don't actually sleep between retries
        patcher_sleep = mock.patch.object(api_client.time, "sleep")
        self.sleep = patcher_sleep.start()
        self.addCleanup(patcher_sleep.stop)

    def _call(self):
        return api_client._call_messages_sync(
            "", [{"role": "user", "content": "x"}], 16, "model")

    def test_success_first_try(self):
        with mock.patch.object(api_client.urllib.request, "urlopen",
                               return_value=_ok_response()) as m:
            text, usage = self._call()
        self.assertEqual(text, "hi")
        self.assertEqual(m.call_count, 1)

    def test_429_then_success(self):
        with mock.patch.object(
                api_client.urllib.request, "urlopen",
                side_effect=[_http_error(429), _ok_response()]) as m:
            text, _ = self._call()
        self.assertEqual(text, "hi")
        self.assertEqual(m.call_count, 2)

    def test_500_then_success(self):
        with mock.patch.object(
                api_client.urllib.request, "urlopen",
                side_effect=[_http_error(500), _ok_response()]) as m:
            text, _ = self._call()
        self.assertEqual(text, "hi")
        self.assertEqual(m.call_count, 2)

    def test_connection_error_then_success(self):
        with mock.patch.object(
                api_client.urllib.request, "urlopen",
                side_effect=[urllib.error.URLError("refused"), _ok_response()]) as m:
            text, _ = self._call()
        self.assertEqual(text, "hi")
        self.assertEqual(m.call_count, 2)

    def test_400_fails_immediately(self):
        with mock.patch.object(
                api_client.urllib.request, "urlopen",
                side_effect=_http_error(400)) as m:
            with self.assertRaises(RuntimeError):
                self._call()
        self.assertEqual(m.call_count, 1)

    def test_persistent_529_exhausts_attempts(self):
        with mock.patch.object(
                api_client.urllib.request, "urlopen",
                side_effect=_http_error(529)) as m:
            with self.assertRaises(RuntimeError):
                self._call()
        self.assertEqual(m.call_count, api_client.MAX_ATTEMPTS)

    def test_missing_api_key_raises(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            del os.environ["ANTHROPIC_API_KEY"]
            with self.assertRaises(RuntimeError):
                self._call()


if __name__ == "__main__":
    unittest.main()
