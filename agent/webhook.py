"""Webhook server — receives external notifications and triggers skills."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 1024 * 1024  # 1MB
BASE_DIR = Path(__file__).parent.parent
FATHOM_WORKSPACE = BASE_DIR / "skills" / "fathom" / "workspace"


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

    title = data.get("title", "Untitled Meeting")
    transcript = data.get("transcript")
    if not transcript:
        logger.info("Fathom webhook: meeting '%s' received (no transcript)", title)
        return "no_transcript"

    # Determine date from payload or fallback to today
    date_str = data.get("created_at") or data.get("recording_start_time") or ""
    if date_str:
        date_prefix = date_str[:10]  # YYYY-MM-DD
    else:
        date_prefix = time.strftime("%Y-%m-%d")

    # Build attendee list from transcript speakers
    speakers = []
    seen = set()
    for entry in transcript:
        name = entry.get("speaker", {}).get("display_name", "Unknown")
        if name not in seen:
            speakers.append(name)
            seen.add(name)

    # Format transcript lines
    lines = [
        f"Meeting: {title}",
        f"Date: {date_prefix}",
        f"Attendees: {', '.join(speakers)}",
        "=" * 80,
        "",
    ]
    for entry in transcript:
        ts = entry.get("timestamp", "00:00:00")
        speaker = entry.get("speaker", {}).get("display_name", "Unknown")
        text = entry.get("text", "")
        lines.append(f"[{ts}] {speaker}:")
        lines.append(text)
        lines.append("")

    content = "\n".join(lines)

    # Save transcript to fathom skill workspace
    FATHOM_WORKSPACE.mkdir(parents=True, exist_ok=True)
    safe_title = _sanitize_filename(title)
    filename = f"{date_prefix}_{safe_title}.txt"
    filepath = FATHOM_WORKSPACE / filename

    filepath.write_text(content)
    logger.info("Fathom transcript saved: %s", filepath)

    # Trigger the fathom skill to summarize
    task = (
        f"Summarize the meeting transcript at: {filepath}\n"
        f"Meeting: {title}\n"
        f"Date: {date_prefix}\n"
        f"Attendees: {', '.join(speakers)}"
    )
    server._trigger_skill("fathom", task)

    return "accepted"


def _sanitize_filename(name: str) -> str:
    """Convert a meeting title to a safe filename slug."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = name.strip("-")
    return name[:80] or "meeting"
