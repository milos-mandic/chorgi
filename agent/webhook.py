"""Webhook server — receives external notifications and triggers skills."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 1024 * 1024  # 1MB
BASE_DIR = Path(__file__).parent.parent

# Lazy-loaded fathom client
_fathom_client = None


def _get_fathom_client():
    global _fathom_client
    if _fathom_client is None:
        sys.path.insert(0, str(BASE_DIR / "skills" / "fathom"))
        import fathom_client as fc
        _fathom_client = fc
    return _fathom_client


class WebhookServer:
    """Lightweight HTTP server for receiving webhooks on a background thread."""

    def __init__(self):
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._orchestrator = None

    def start(self, loop: asyncio.AbstractEventLoop, orchestrator) -> None:
        """Start the webhook server on a daemon thread."""
        secret = os.environ.get("WEBHOOK_SECRET", "")
        if not secret:
            logger.info("Webhook server disabled (WEBHOOK_SECRET not set)")
            return

        self._loop = loop
        self._orchestrator = orchestrator

        port = int(os.environ.get("WEBHOOK_PORT", "8443"))
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                logger.info("Webhook HTTP: " + format, *args)

            def do_GET(self):
                parts = _parse_path(self.path, secret)
                if parts is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                _sec, route = parts
                if route == "health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok"}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                logger.info("Webhook POST received: path=%s", self.path)
                parts = _parse_path(self.path, secret)
                if parts is None:
                    logger.warning("Webhook POST path mismatch: %s", self.path)
                    self.send_response(404)
                    self.end_headers()
                    return

                _sec, route = parts

                # Read body with size limit
                length = int(self.headers.get("Content-Length", 0))
                if length > MAX_BODY_SIZE:
                    self.send_response(413)
                    self.end_headers()
                    return

                body = self.rfile.read(length) if length > 0 else b""
                headers = {k.lower(): v for k, v in self.headers.items()}

                if route == "fathom":
                    result = _handle_fathom(headers, body, server_self)
                    if result is None:
                        self.send_response(400)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"accepted"}')
                else:
                    self.send_response(404)
                    self.end_headers()

        self._server = HTTPServer(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="webhook-server",
        )
        self._thread.start()
        logger.info("Webhook server started on port %d", port)

    def stop(self) -> None:
        """Shut down the webhook server."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Webhook server stopped")

    def _trigger_skill(self, skill: str, task: str) -> None:
        """Bridge from the HTTP thread to the async orchestrator."""
        if self._loop and self._orchestrator:
            future = asyncio.run_coroutine_threadsafe(
                self._orchestrator.trigger_webhook_skill(skill, task),
                self._loop,
            )
            # Don't block — fire and forget. Errors are logged inside the coroutine.
            future.add_done_callback(_log_future_error)


def _log_future_error(future):
    """Callback to log exceptions from fire-and-forget coroutines."""
    try:
        future.result()
    except Exception:
        logger.error("Webhook skill trigger failed", exc_info=True)


def _parse_path(path: str, secret: str) -> tuple[str, str] | None:
    """Parse /<secret>/<route> from path. Returns None on mismatch."""
    stripped = path.strip("/")
    parts = stripped.split("/", 1)
    if len(parts) != 2 or parts[0] != secret:
        return None
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# Fathom handler
# ---------------------------------------------------------------------------

def _verify_fathom(headers: dict[str, str], body: bytes) -> bool:
    """HMAC-SHA256 signature verification with replay protection."""
    secret = os.environ.get("FATHOM_WEBHOOK_SECRET", "")
    if not secret:
        return True  # No secret configured, skip verification

    msg_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signature = headers.get("webhook-signature", "")

    if not msg_id or not timestamp or not signature:
        logger.warning("Fathom verify: missing headers — id=%r ts=%r sig=%r", msg_id, timestamp, signature)
        return False

    # Replay protection: reject timestamps older than 5 minutes
    try:
        ts = int(timestamp)
        drift = abs(time.time() - ts)
        if drift > 300:
            logger.warning("Fathom verify: timestamp too old (drift=%.0fs)", drift)
            return False
    except (ValueError, TypeError):
        logger.warning("Fathom verify: invalid timestamp %r", timestamp)
        return False

    # Strip whsec_ prefix and base64-decode the secret
    raw_secret = secret
    if raw_secret.startswith("whsec_"):
        raw_secret = raw_secret[6:]
    try:
        secret_bytes = base64.b64decode(raw_secret)
    except Exception:
        return False

    # Sign: "{msg_id}.{timestamp}.{body}"
    signed_content = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

    # Signature header may have multiple space-delimited sigs, each prefixed "v1,"
    for sig in signature.split(" "):
        if sig.startswith("v1,"):
            if hmac.compare_digest(sig[3:], expected):
                return True
    return False


def _handle_fathom(headers: dict[str, str], body: bytes, server: WebhookServer) -> str | None:
    """Process a Fathom webhook payload. Returns a status string or None on error."""
    if not _verify_fathom(headers, body):
        logger.warning("Fathom webhook signature verification failed")
        return None

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Fathom webhook: malformed JSON payload")
        return None

    fc = _get_fathom_client()
    parsed = fc.parse_fathom_payload(data)
    if parsed is None:
        title = data.get("title", "Untitled Meeting")
        logger.info("Fathom webhook: meeting '%s' received (no transcript)", title)
        return "no_transcript"

    content = fc.format_transcript(parsed)
    filepath = fc.save_transcript(content, parsed["date"], parsed["title"])
    logger.info("Fathom transcript saved: %s", filepath)

    # Trigger the fathom skill to summarize
    task = (
        f"Summarize the latest meeting transcript.\n"
        f"Meeting: {parsed['title']}\n"
        f"Date: {parsed['date']}\n"
        f"Attendees: {', '.join(parsed['speakers'])}"
    )
    server._trigger_skill("fathom", task)

    return "accepted"
