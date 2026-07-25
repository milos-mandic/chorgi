"""Tests for webhook pure helpers: secret paths, Fathom HMAC, body length."""

import base64
import hashlib
import hmac
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webhook import (
    MAX_BODY_SIZE,
    WebhookServer,
    _parse_content_length,
    _parse_secret_path,
    _verify_fathom,
)


class TestParseSecretPath(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(_parse_secret_path("/s3cret/health", "s3cret"),
                         ("s3cret", "health"))

    def test_wrong_secret(self):
        self.assertIsNone(_parse_secret_path("/nope/health", "s3cret"))

    def test_no_route(self):
        self.assertIsNone(_parse_secret_path("/s3cret", "s3cret"))

    def test_nested_route(self):
        self.assertEqual(_parse_secret_path("/s/a/b", "s"), ("s", "a/b"))


class TestParseContentLength(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(_parse_content_length("42"), 42)

    def test_missing(self):
        self.assertEqual(_parse_content_length(None), 0)

    def test_garbage(self):
        self.assertEqual(_parse_content_length("abc"), 0)

    def test_negative(self):
        self.assertEqual(_parse_content_length("-5"), 0)

    def test_oversized(self):
        self.assertEqual(_parse_content_length(str(MAX_BODY_SIZE + 1)), 0)


class TestVerifyFathom(unittest.TestCase):
    SECRET_BYTES = b"test-signing-key"

    def _env(self):
        b64 = base64.b64encode(self.SECRET_BYTES).decode()
        return mock.patch.dict(
            "os.environ", {"FATHOM_WEBHOOK_SECRET": f"whsec_{b64}"})

    def _sign(self, msg_id: str, timestamp: str, body: bytes) -> str:
        signed = f"{msg_id}.{timestamp}.".encode() + body
        digest = hmac.new(self.SECRET_BYTES, signed, hashlib.sha256).digest()
        return "v1," + base64.b64encode(digest).decode()

    def test_no_secret_configured_passes(self):
        with mock.patch.dict("os.environ", {"FATHOM_WEBHOOK_SECRET": ""}):
            self.assertTrue(_verify_fathom({}, b"{}"))

    def test_valid_signature(self):
        body = b'{"ok": true}'
        ts = str(int(time.time()))
        headers = {"webhook-id": "m1", "webhook-timestamp": ts,
                   "webhook-signature": self._sign("m1", ts, body)}
        with self._env():
            self.assertTrue(_verify_fathom(headers, body))

    def test_tampered_body_rejected(self):
        ts = str(int(time.time()))
        headers = {"webhook-id": "m1", "webhook-timestamp": ts,
                   "webhook-signature": self._sign("m1", ts, b"original")}
        with self._env():
            self.assertFalse(_verify_fathom(headers, b"tampered"))

    def test_stale_timestamp_rejected(self):
        body = b"{}"
        ts = str(int(time.time()) - 600)
        headers = {"webhook-id": "m1", "webhook-timestamp": ts,
                   "webhook-signature": self._sign("m1", ts, body)}
        with self._env():
            self.assertFalse(_verify_fathom(headers, body))

    def test_missing_headers_rejected(self):
        with self._env():
            self.assertFalse(_verify_fathom({}, b"{}"))


class TestHealthPayload(unittest.TestCase):
    def test_no_orchestrator_is_ok(self):
        server = WebhookServer()
        self.assertEqual(server.health_payload()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
