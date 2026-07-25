"""Webhook server — receives external notifications and serves the dashboard UI.

HTTP plumbing, static serving, SSE chat streaming, and the Fathom webhook live
here; the JSON API route handlers live in agent/api_handlers.py.

Routes:
  Webhook (secret-prefixed, machine-to-machine):
    GET  /<WEBHOOK_SECRET>/health        → liveness
    POST /<WEBHOOK_SECRET>/fathom        → Fathom meeting transcripts

  UI + API (auth handled at edge by Cloudflare Access):
    GET    /                             → dashboard HTML
    GET    /app.js, /style.css           → static assets
    GET    /api/state                    → combined snapshot
    GET    /api/tasks                    → list tasks
    POST   /api/tasks                    → create task
    PATCH  /api/tasks/<id>               → update task
    DELETE /api/tasks/<id>               → remove task
    GET    /api/bookmarks                → list bookmarks
    POST   /api/bookmarks                → create bookmark
    DELETE /api/bookmarks                → remove (body: {url})
    GET    /api/linkedin/calendar        → current week's calendar
    GET    /api/wiki/topics              → list topics (no article body)
    GET    /api/wiki/topics/<id|slug>    → topic + hydrated source bookmarks
    POST   /api/wiki/recluster           → queue full recluster + resynthesis
    POST   /api/wiki/topics/<id>/resynthesize → queue article regeneration
    DELETE /api/wiki/topics/<id>         → remove a topic
    POST   /api/trigger                  → {skill, task} → run a subagent
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from agent import api_handlers

logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 1024 * 1024  # 1MB
BASE_DIR = Path(__file__).parent.parent
UI_DIR = Path(__file__).parent / "ui"

# Lazy-loaded skill module (Fathom transcripts only; the API-facing loaders
# live in api_handlers)
_fathom_client = None


def _get_fathom_client():
    global _fathom_client
    if _fathom_client is None:
        sys.path.insert(0, str(BASE_DIR / "skills" / "fathom"))
        import fathom_client as fc
        _fathom_client = fc
    return _fathom_client


class _ReusableHTTPServer(ThreadingHTTPServer):
    # Threaded so a long-running streaming chat request can't block dashboard
    # polling or other API calls. daemon_threads lets the process exit cleanly.
    allow_reuse_address = True
    daemon_threads = True


class WebhookServer:
    """Lightweight HTTP server for webhooks + UI on a background thread."""

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

        try:
            port = int(os.environ.get("WEBHOOK_PORT", "8443"))
        except ValueError:
            # A typo in secrets.env must not crash startup into a launchd loop.
            logger.error(
                "Invalid WEBHOOK_PORT %r — falling back to 8443",
                os.environ.get("WEBHOOK_PORT"),
            )
            port = 8443
        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                msg = format % args
                # The dashboard polls GET endpoints every few seconds and
                # pulls static assets on every load — DEBUG, not INFO, or the
                # log fills with thousands of identical lines per day.
                # Mutations (POST/PATCH/DELETE) stay at INFO.
                if '"GET /' in msg:
                    logger.debug("Webhook HTTP: %s", msg)
                else:
                    logger.info("Webhook HTTP: %s", msg)

            # ---- helpers ----------------------------------------------------
            def _send_json(self, status: int, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_text(self, status: int, body: str, content_type: str = "text/plain; charset=utf-8"):
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read_body(self) -> bytes:
                length = _parse_content_length(self.headers.get("Content-Length"))
                return self.rfile.read(length) if length > 0 else b""

            def _read_json(self):
                try:
                    body = self._read_body()
                    return json.loads(body) if body else {}
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return None

            def _check_cf_access(self):
                """Soft warning if CF Access header is missing — does not block."""
                email = self.headers.get("Cf-Access-Authenticated-User-Email")
                if not email:
                    logger.warning(
                        "UI/API request without Cf-Access-Authenticated-User-Email "
                        "(path=%s, client=%s)", self.path, self.client_address[0]
                    )

            # ---- dispatch ---------------------------------------------------
            def do_GET(self):
                path = self.path.split("?", 1)[0]

                # Webhook secret routes
                parts = _parse_secret_path(path, secret)
                if parts is not None:
                    _sec, route = parts
                    if route == "health":
                        self._send_json(200, server_self.health_payload())
                    else:
                        self.send_response(404); self.end_headers()
                    return

                # UI routes — served at root
                if path == "/" or path == "/index.html":
                    self._check_cf_access()
                    self._serve_static("index.html", "text/html; charset=utf-8")
                    return
                if path == "/app.js":
                    self._check_cf_access()
                    self._serve_static("app.js", "application/javascript; charset=utf-8")
                    return
                if path == "/style.css":
                    self._check_cf_access()
                    self._serve_static("style.css", "text/css; charset=utf-8")
                    return
                if path == "/chorgi_bot.png":
                    self._check_cf_access()
                    self._serve_static("chorgi_bot.png", "image/png")
                    return

                # API routes
                if path.startswith("/api/"):
                    self._check_cf_access()
                    self._api_get(path)
                    return

                self.send_response(404); self.end_headers()

            def do_POST(self):
                path = self.path.split("?", 1)[0]

                # Webhook secret routes (Fathom)
                parts = _parse_secret_path(path, secret)
                if parts is not None:
                    _sec, route = parts
                    if route == "fathom":
                        logger.info("Fathom webhook received")
                        body = self._read_body()
                        headers = {k.lower(): v for k, v in self.headers.items()}
                        result = _handle_fathom(headers, body, server_self)
                        if result is None:
                            self.send_response(400); self.end_headers()
                            return
                        self._send_json(200, {"status": "accepted"})
                    else:
                        self.send_response(404); self.end_headers()
                    return

                # Local chat streaming send — bypasses the JSON wrapper so it can
                # stream Server-Sent Events back as the model generates.
                if (path.startswith("/api/chat/conversations/")
                        and path.endswith("/messages")):
                    self._check_cf_access()
                    cid = path[len("/api/chat/conversations/"):-len("/messages")]
                    self._api_chat_stream(cid)
                    return

                # API POST/PATCH/DELETE
                if path.startswith("/api/"):
                    self._check_cf_access()
                    self._api_write(path, "POST")
                    return

                self.send_response(404); self.end_headers()

            # ---- Local chat streaming ---------------------------------------
            def _api_chat_stream(self, cid: str):
                """Append the user message, then stream the assistant reply as SSE."""
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"error": "bad json"}); return
                content = (body.get("content") or "").strip()
                if not content:
                    self._send_json(400, {"error": "content required"}); return

                lc = api_handlers.get_local_chat()
                conv = lc.append_message(cid, "user", content)
                messages = [{"role": m["role"], "content": m["content"]}
                            for m in conv.get("messages", [])]

                # Headers for an event stream. X-Accel-Buffering disables proxy
                # buffering so tokens arrive promptly through Cloudflare.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()

                def emit(obj):
                    self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
                    self.wfile.flush()

                full = []
                try:
                    for delta in lc.stream_completion(messages, conv.get("model")):
                        full.append(delta)
                        emit({"delta": delta})
                except Exception as e:
                    logger.error("Local chat stream failed: %s", e, exc_info=True)
                    emit({"error": str(e)})
                else:
                    # Only persist the assistant turn if generation succeeded.
                    lc.append_message(conv["id"], "assistant", "".join(full))
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_PATCH(self):
                path = self.path.split("?", 1)[0]
                if path.startswith("/api/"):
                    self._check_cf_access()
                    self._api_write(path, "PATCH")
                    return
                self.send_response(404); self.end_headers()

            def do_DELETE(self):
                path = self.path.split("?", 1)[0]
                if path.startswith("/api/"):
                    self._check_cf_access()
                    self._api_write(path, "DELETE")
                    return
                self.send_response(404); self.end_headers()

            # ---- static -----------------------------------------------------
            def _serve_static(self, filename: str, content_type: str):
                fp = UI_DIR / filename
                if not fp.exists():
                    self._send_text(404, f"Not found: {filename}")
                    return
                data = fp.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                # no-store (not no-cache): Cloudflare caches .js/.css by
                # extension and may serve stale assets after a deploy.
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            # ---- API dispatch (handlers live in agent/api_handlers.py) ------
            def _api_get(self, path: str):
                status, payload = api_handlers.api_get(path)
                self._send_json(status, payload)

            def _api_write(self, path: str, method: str):
                body = self._read_json()
                status, payload = api_handlers.api_write(path, method, body, server_self)
                self._send_json(status, payload)

        # Kill any stale bot instance holding the port. Only processes that are
        # verifiably a previous chorgi bot — SIGKILLing an arbitrary process
        # that happens to hold the port could corrupt unrelated state.
        result = subprocess.run(["/usr/sbin/lsof", "-ti", f":{port}"], capture_output=True, text=True)
        if result.stdout.strip():
            my_pid = str(os.getpid())
            killed_any = False
            for pid in result.stdout.strip().split("\n"):
                pid = pid.strip()
                if not pid or pid == my_pid:
                    continue
                ps = subprocess.run(
                    ["/bin/ps", "-p", pid, "-o", "command="],
                    capture_output=True, text=True,
                )
                if "chorgi_bot/agent/main.py" not in ps.stdout:
                    logger.warning(
                        "Port %d held by non-bot process %s (%s) — not killing; "
                        "bind will likely fail", port, pid, ps.stdout.strip()[:120],
                    )
                    continue
                logger.warning("Terminating stale bot instance %s on port %d", pid, port)
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    killed_any = True
                except (ProcessLookupError, ValueError):
                    pass
            if killed_any:
                time.sleep(1.0)
                # SIGKILL any that ignored SIGTERM
                still = subprocess.run(["/usr/sbin/lsof", "-ti", f":{port}"], capture_output=True, text=True)
                for pid in still.stdout.strip().split("\n"):
                    pid = pid.strip()
                    if pid and pid != my_pid:
                        try:
                            os.kill(int(pid), signal.SIGKILL)
                        except (ProcessLookupError, ValueError):
                            pass
                time.sleep(0.5)

        try:
            self._server = _ReusableHTTPServer(("0.0.0.0", port), Handler)
        except OSError as e:
            logger.error("Webhook server failed to bind port %d: %s (continuing without webhooks)", port, e)
            orchestrator.startup_warnings.append(
                f"Webhook server failed to bind port {port} ({e}). "
                "Fathom webhooks and the dashboard are DOWN until the bot restarts cleanly."
            )
            return

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="webhook-server",
        )
        self._thread.start()
        logger.info("Webhook server started on port %d", port)

    def health_payload(self) -> dict:
        """Liveness with substance: heartbeat age/duration + agent slots."""
        payload = {"status": "ok"}
        orch = self._orchestrator
        sched = getattr(orch, "scheduler", None) if orch else None
        if sched is not None and getattr(sched, "last_heartbeat_at", None):
            age = (datetime.now(timezone.utc) - sched.last_heartbeat_at).total_seconds()
            payload["last_heartbeat_age_s"] = round(age)
            payload["heartbeat_duration_s"] = round(
                sched.last_heartbeat_duration_s or 0, 1)
            if age > 15 * 60:
                payload["status"] = "degraded"
        if orch is not None and hasattr(orch, "_semaphore"):
            payload["agent_slots_free"] = orch._semaphore._value
        return payload

    def stop(self) -> None:
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
            future.add_done_callback(_log_future_error)

    def _trigger_wiki(self, action: str, topic_id: str | None) -> tuple[bool, str]:
        """Bridge from HTTP thread to async wiki ops. Returns (queued, reason)."""
        if not self._loop:
            return False, "not ready"
        from agent.knowledge import wiki as wiki_mod
        if action == "recluster":
            # Pre-check so the dashboard gets immediate cooldown feedback;
            # run_maintenance re-checks on the event loop either way.
            ok, reason = wiki_mod.maintenance_available()
            if not ok:
                return False, reason
            coro = wiki_mod.run_maintenance()
        elif action == "resynthesize" and topic_id:
            coro = wiki_mod.resynthesize_topic(topic_id)
        else:
            return False, "unknown action"
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(_log_future_error)
        return True, ""


def _log_future_error(future):
    try:
        future.result()
    except Exception:
        logger.error("Webhook skill trigger failed", exc_info=True)


def _parse_content_length(value) -> int:
    """Safe Content-Length parse: garbage or oversized headers read as 0."""
    try:
        length = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if length < 0 or length > MAX_BODY_SIZE:
        return 0
    return length


def _parse_secret_path(path: str, secret: str) -> tuple[str, str] | None:
    """Parse /<secret>/<route> from path. Returns None on mismatch."""
    stripped = path.strip("/")
    parts = stripped.split("/", 1)
    if len(parts) != 2 or parts[0] != secret:
        return None
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# Fathom handler (unchanged)
# ---------------------------------------------------------------------------

def _verify_fathom(headers: dict[str, str], body: bytes) -> bool:
    secret = os.environ.get("FATHOM_WEBHOOK_SECRET", "")
    if not secret:
        return True

    msg_id = headers.get("webhook-id", "")
    timestamp = headers.get("webhook-timestamp", "")
    signature = headers.get("webhook-signature", "")

    if not msg_id or not timestamp or not signature:
        logger.warning("Fathom verify: missing headers — id=%r ts=%r sig=%r", msg_id, timestamp, signature)
        return False

    try:
        ts = int(timestamp)
        drift = abs(time.time() - ts)
        if drift > 300:
            logger.warning("Fathom verify: timestamp too old (drift=%.0fs)", drift)
            return False
    except (ValueError, TypeError):
        logger.warning("Fathom verify: invalid timestamp %r", timestamp)
        return False

    raw_secret = secret
    if raw_secret.startswith("whsec_"):
        raw_secret = raw_secret[6:]
    try:
        secret_bytes = base64.b64decode(raw_secret)
    except Exception:
        return False

    signed_content = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode()

    for sig in signature.split(" "):
        if sig.startswith("v1,"):
            if hmac.compare_digest(sig[3:], expected):
                return True
    return False


def _handle_fathom(headers: dict[str, str], body: bytes, server: WebhookServer) -> str | None:
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
    # Legacy save (kept so the `fathom` skill can still do ad-hoc transcript
    # search/read against its own archive).
    legacy_path = fc.save_transcript(content, parsed["date"], parsed["title"])
    logger.info("Fathom transcript saved (legacy): %s", legacy_path)

    # New M1 path: pre-allocate an interaction_id and write the transcript to
    # .personal/content/meetings/{id}.md, then hand the post_meeting skill the
    # full context so it can record + propose inbox items.
    try:
        from agent.knowledge.ids import ulid
        from agent.knowledge.content import meeting_path, write_atomic
        interaction_id = ulid()
        mp = meeting_path(interaction_id)
        write_atomic(mp, content)
    except Exception:
        logger.exception("Failed to stage meeting content for post_meeting skill")
        return "accepted"

    # Build an attendees payload. fathom_client only gives names (`speakers`);
    # use empty emails so match-person falls back to name lookup.
    attendees = [{"name": n, "email": ""} for n in parsed.get("speakers", [])]

    task = (
        "A Fathom meeting just landed. Read the transcript, then record the "
        "interaction and propose inbox items per your CLAUDE.md workflow.\n\n"
        f"interaction_id: {interaction_id}\n"
        f"transcript_path: {mp}\n"
        f"title: {parsed['title']}\n"
        f"occurred_at: {parsed['date']}\n"
        f"attendees_json: {json.dumps(attendees)}"
    )
    server._trigger_skill("post_meeting", task)

    return "accepted"
