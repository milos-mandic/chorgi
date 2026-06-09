"""Webhook server — receives external notifications and serves the dashboard UI.

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
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BODY_SIZE = 1024 * 1024  # 1MB
BASE_DIR = Path(__file__).parent.parent
UI_DIR = Path(__file__).parent / "ui"

# Lazy-loaded skill modules
_fathom_client = None
_task_cli = None
_bookmarks_cli = None
_local_chat = None

# Single lock for all JSON mutations (tasks + bookmarks).
# These files are tiny; a coarse lock keeps things simple and safe.
_data_lock = threading.Lock()


def _get_fathom_client():
    global _fathom_client
    if _fathom_client is None:
        sys.path.insert(0, str(BASE_DIR / "skills" / "fathom"))
        import fathom_client as fc
        _fathom_client = fc
    return _fathom_client


def _get_task_cli():
    global _task_cli
    if _task_cli is None:
        sys.path.insert(0, str(BASE_DIR / "skills" / "tasks"))
        import task_cli as tc
        _task_cli = tc
    return _task_cli


def _get_bookmarks_cli():
    global _bookmarks_cli
    if _bookmarks_cli is None:
        sys.path.insert(0, str(BASE_DIR / "skills" / "bookmarks"))
        import bookmarks_cli as bc
        _bookmarks_cli = bc
    return _bookmarks_cli


def _get_local_chat():
    global _local_chat
    if _local_chat is None:
        from agent import local_chat as lc
        _local_chat = lc
    return _local_chat


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

        port = int(os.environ.get("WEBHOOK_PORT", "8443"))
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
                length = int(self.headers.get("Content-Length", 0))
                if length > MAX_BODY_SIZE:
                    return b""
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
                        self._send_json(200, {"status": "ok"})
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

                lc = _get_local_chat()
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
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)

            # ---- API GET ----------------------------------------------------
            def _api_get(self, path: str):
                try:
                    if path == "/api/state":
                        self._send_json(200, _build_state())
                    elif path == "/api/tasks":
                        tc = _get_task_cli()
                        self._send_json(200, {"tasks": tc.load_tasks()})
                    elif path == "/api/bookmarks":
                        bc = _get_bookmarks_cli()
                        self._send_json(200, {"bookmarks": bc.load_bookmarks()})
                    elif path == "/api/linkedin/calendar":
                        self._send_json(200, _load_linkedin_calendar())
                    elif path == "/api/people":
                        from agent.knowledge import models as km
                        self._send_json(200, {"people": km.list_people()})
                    elif path.startswith("/api/people/"):
                        pid = path[len("/api/people/"):]
                        from agent.knowledge import models as km
                        person = km.get_person(pid)
                        if not person:
                            self._send_json(404, {"error": "person not found"}); return
                        person["interactions"] = km.person_interactions(pid)
                        self._send_json(200, person)
                    elif path == "/api/inbox":
                        from agent.knowledge import models as km
                        self._send_json(200, {"inbox": km.list_inbox()})
                    elif path == "/api/wiki/topics":
                        from agent.knowledge import models as km
                        self._send_json(200, {"topics": km.list_topics()})
                    elif path.startswith("/api/wiki/topics/"):
                        ident = path[len("/api/wiki/topics/"):]
                        topic = _get_wiki_topic(ident)
                        if topic is None:
                            self._send_json(404, {"error": "topic not found"}); return
                        self._send_json(200, topic)
                    elif path == "/api/chat/config":
                        self._send_json(200, _get_local_chat().config())
                    elif path == "/api/chat/conversations":
                        lc = _get_local_chat()
                        self._send_json(200, {"conversations": lc.list_conversations()})
                    elif path.startswith("/api/chat/conversations/"):
                        cid = path[len("/api/chat/conversations/"):]
                        conv = _get_local_chat().get_conversation(cid)
                        if conv is None:
                            self._send_json(404, {"error": "conversation not found"}); return
                        self._send_json(200, conv)
                    else:
                        self._send_json(404, {"error": "not found"})
                except Exception as e:
                    logger.error("API GET %s failed: %s", path, e, exc_info=True)
                    self._send_json(500, {"error": str(e)})

            # ---- API write --------------------------------------------------
            def _api_write(self, path: str, method: str):
                try:
                    # Tasks
                    if path == "/api/tasks" and method == "POST":
                        body = self._read_json()
                        if body is None:
                            self._send_json(400, {"error": "bad json"}); return
                        self._send_json(200, _create_task(body))
                        return

                    if path.startswith("/api/tasks/"):
                        task_id = path[len("/api/tasks/"):]
                        if method == "PATCH":
                            body = self._read_json()
                            if body is None:
                                self._send_json(400, {"error": "bad json"}); return
                            updated = _update_task(task_id, body)
                            if updated is None:
                                self._send_json(404, {"error": "task not found"}); return
                            self._send_json(200, updated)
                            return
                        if method == "DELETE":
                            ok = _delete_task(task_id)
                            self._send_json(200 if ok else 404, {"deleted": ok})
                            return

                    # Bookmarks
                    if path == "/api/bookmarks" and method == "POST":
                        body = self._read_json()
                        if body is None:
                            self._send_json(400, {"error": "bad json"}); return
                        self._send_json(200, _create_bookmark(body))
                        return

                    if path == "/api/bookmarks" and method == "DELETE":
                        body = self._read_json()
                        if body is None or not body.get("url"):
                            self._send_json(400, {"error": "url required"}); return
                        ok = _delete_bookmark(body["url"])
                        self._send_json(200 if ok else 404, {"deleted": ok})
                        return

                    # Inbox accept / reject
                    if path.startswith("/api/inbox/") and method == "POST":
                        rest = path[len("/api/inbox/"):]
                        item_id, _, action = rest.partition("/")
                        from agent.knowledge import models as km
                        if action == "accept":
                            res = km.accept_inbox_item(item_id)
                            if res is None:
                                self._send_json(404, {"error": "item not pending or missing"}); return
                            self._send_json(200, res)
                            return
                        if action == "reject":
                            ok = km.reject_inbox_item(item_id)
                            self._send_json(200 if ok else 404, {"rejected": ok})
                            return

                    # Wiki
                    if path == "/api/wiki/recluster" and method == "POST":
                        server_self._trigger_wiki("recluster", None)
                        self._send_json(200, {"queued": True})
                        return

                    if path.startswith("/api/wiki/topics/") and method == "POST":
                        rest = path[len("/api/wiki/topics/"):]
                        topic_id, _, action = rest.partition("/")
                        if action == "resynthesize":
                            server_self._trigger_wiki("resynthesize", topic_id)
                            self._send_json(200, {"queued": True, "topic_id": topic_id})
                            return

                    if path.startswith("/api/wiki/topics/") and method == "DELETE":
                        topic_id = path[len("/api/wiki/topics/"):]
                        from agent.knowledge import models as km
                        ok = km.delete_topic(topic_id)
                        self._send_json(200 if ok else 404, {"deleted": ok})
                        return

                    # Local chat — create / delete conversations.
                    # (The streaming message send is handled in do_POST.)
                    if path == "/api/chat/conversations" and method == "POST":
                        self._send_json(200, _get_local_chat().create_conversation())
                        return

                    if path.startswith("/api/chat/conversations/") and method == "DELETE":
                        cid = path[len("/api/chat/conversations/"):]
                        ok = _get_local_chat().delete_conversation(cid)
                        self._send_json(200 if ok else 404, {"deleted": ok})
                        return

                    # Trigger subagent
                    if path == "/api/trigger" and method == "POST":
                        body = self._read_json()
                        if body is None:
                            self._send_json(400, {"error": "bad json"}); return
                        skill = body.get("skill")
                        task = body.get("task")
                        if not skill or not task:
                            self._send_json(400, {"error": "skill and task required"}); return
                        server_self._trigger_skill(skill, task)
                        self._send_json(200, {"status": "queued", "skill": skill})
                        return

                    self._send_json(404, {"error": "not found"})
                except Exception as e:
                    logger.error("API %s %s failed: %s", method, path, e, exc_info=True)
                    self._send_json(500, {"error": str(e)})

        # Kill any stale process holding the port (e.g. previous bot instance)
        result = subprocess.run(["/usr/sbin/lsof", "-ti", f":{port}"], capture_output=True, text=True)
        if result.stdout.strip():
            my_pid = str(os.getpid())
            for pid in result.stdout.strip().split("\n"):
                pid = pid.strip()
                if pid and pid != my_pid:
                    logger.warning("Killing stale process %s on port %d", pid, port)
                    try:
                        os.kill(int(pid), 9)
                    except (ProcessLookupError, ValueError):
                        pass
            time.sleep(0.5)

        try:
            self._server = _ReusableHTTPServer(("0.0.0.0", port), Handler)
        except OSError as e:
            logger.error("Webhook server failed to bind port %d: %s (continuing without webhooks)", port, e)
            return

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="webhook-server",
        )
        self._thread.start()
        logger.info("Webhook server started on port %d", port)

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

    def _trigger_wiki(self, action: str, topic_id: str | None) -> None:
        """Bridge from HTTP thread to async wiki ops. Returns immediately."""
        if not self._loop:
            return
        from agent.knowledge import wiki as wiki_mod
        if action == "recluster":
            coro = wiki_mod.run_maintenance()
        elif action == "resynthesize" and topic_id:
            coro = wiki_mod.resynthesize_topic(topic_id)
        else:
            return
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(_log_future_error)


def _log_future_error(future):
    try:
        future.result()
    except Exception:
        logger.error("Webhook skill trigger failed", exc_info=True)


def _parse_secret_path(path: str, secret: str) -> tuple[str, str] | None:
    """Parse /<secret>/<route> from path. Returns None on mismatch."""
    stripped = path.strip("/")
    parts = stripped.split("/", 1)
    if len(parts) != 2 or parts[0] != secret:
        return None
    return parts[0], parts[1]


# ---------------------------------------------------------------------------
# State + mutation helpers (importable so the UI shares the CLI write path)
# ---------------------------------------------------------------------------

def _build_state() -> dict:
    tc = _get_task_cli()
    bc = _get_bookmarks_cli()
    with _data_lock:
        tasks = tc.load_tasks()
        bookmarks = bc.load_bookmarks()
    people = []
    inbox = []
    try:
        from agent.knowledge import models as km
        people = km.list_people()
        inbox = km.list_inbox()
    except Exception as e:
        logger.warning("Knowledge state unavailable: %s", e)
    return {
        "tasks": tasks,
        "bookmarks": bookmarks,
        "linkedin_week": _load_linkedin_calendar(),
        "people": people,
        "inbox": inbox,
        "now": datetime.now(timezone.utc).isoformat(),
    }


def _create_task(body: dict) -> dict:
    tc = _get_task_cli()
    title = (body.get("title") or "").strip()
    if not title:
        return {"error": "title required"}
    tags = body.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    scheduled_at = (body.get("scheduled_at") or "").strip() or None
    with _data_lock:
        tasks = tc.load_tasks()
        task = {
            "id": tc.make_id(),
            "title": title,
            "notes": body.get("notes", "") or "",
            "priority": body.get("priority", "medium") or "medium",
            "estimated_minutes": body.get("estimated_minutes"),
            "deadline": body.get("deadline"),
            "tags": tags,
            "status": body.get("status", "pending") or "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "carry_count": 0,
        }
        if scheduled_at:
            result = tc.create_calendar_event_for_task(task, scheduled_at)
            if result.get("ok"):
                task["status"] = "scheduled"
                task["scheduled_at"] = result["scheduled_at"]
                task["calendar_event_id"] = result["event_id"]
            else:
                task["_calendar_warning"] = result.get("error", "calendar create failed")
        tasks.insert(0, task)
        tc.save_tasks(tasks)
    return task


_TASK_FIELDS = {"title", "notes", "priority", "estimated_minutes",
                "deadline", "tags", "status", "carry_count",
                "scheduled_at", "calendar_event_id"}


def _update_task(task_id: str, body: dict) -> dict | None:
    tc = _get_task_cli()
    with _data_lock:
        tasks = tc.load_tasks()
        task = tc.find_task(tasks, task_id)
        if task is None:
            return None

        new_scheduled = body.get("scheduled_at") if "scheduled_at" in body else "__unset__"
        if new_scheduled != "__unset__":
            new_scheduled = (new_scheduled or "").strip() or None
            old_scheduled = task.get("scheduled_at")
            old_event_id = task.get("calendar_event_id")
            if new_scheduled and new_scheduled != old_scheduled:
                if old_event_id:
                    # Apply pending edits (title/notes/estimate) before updating the event
                    for k, v in body.items():
                        if k in _TASK_FIELDS and k not in ("scheduled_at", "calendar_event_id", "status"):
                            if k == "tags" and isinstance(v, str):
                                v = [t.strip() for t in v.split(",") if t.strip()]
                            task[k] = v
                    result = tc.update_calendar_event_for_task(task, new_scheduled)
                    if result.get("ok"):
                        task["scheduled_at"] = result["scheduled_at"]
                        task["status"] = "scheduled"
                else:
                    result = tc.create_calendar_event_for_task(task, new_scheduled)
                    if result.get("ok"):
                        task["scheduled_at"] = result["scheduled_at"]
                        task["calendar_event_id"] = result["event_id"]
                        task["status"] = "scheduled"
            elif not new_scheduled and old_event_id:
                tc.delete_calendar_event_for_task(task)
                task.pop("calendar_event_id", None)
                task.pop("scheduled_at", None)
                if task.get("status") == "scheduled":
                    task["status"] = "pending"

        for k, v in body.items():
            if k in _TASK_FIELDS and k not in ("scheduled_at", "calendar_event_id"):
                if k == "tags" and isinstance(v, str):
                    v = [t.strip() for t in v.split(",") if t.strip()]
                task[k] = v
        if body.get("status") == "done" and "completed_at" not in task:
            task["completed_at"] = datetime.now(timezone.utc).isoformat()
        tc.save_tasks(tasks)
    return task


def _delete_task(task_id: str) -> bool:
    tc = _get_task_cli()
    with _data_lock:
        tasks = tc.load_tasks()
        task = tc.find_task(tasks, task_id)
        if task is None:
            return False
        if task.get("calendar_event_id"):
            tc.delete_calendar_event_for_task(task)
        tasks.remove(task)
        tc.save_tasks(tasks)
    return True


def _create_bookmark(body: dict) -> dict:
    bc = _get_bookmarks_cli()
    url = (body.get("url") or "").strip()
    if not url:
        return {"error": "url required"}
    tags = body.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    with _data_lock:
        bookmarks = bc.load_bookmarks()
        for b in bookmarks:
            if b["url"] == url:
                return b  # idempotent
        bookmark = {
            "url": url,
            "title": body.get("title", "") or "",
            "tags": tags,
            "notes": body.get("notes", "") or "",
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        bookmarks.insert(0, bookmark)
        bc.save_bookmarks(bookmarks)
    return bookmark


def _delete_bookmark(url: str) -> bool:
    bc = _get_bookmarks_cli()
    with _data_lock:
        bookmarks = bc.load_bookmarks()
        before = len(bookmarks)
        bookmarks = [b for b in bookmarks if b["url"] != url]
        if len(bookmarks) == before:
            return False
        bc.save_bookmarks(bookmarks)
    return True


def _get_wiki_topic(ident: str) -> dict | None:
    """Look up a topic by id or slug, hydrate bookmarks from both stores."""
    from agent.knowledge import models as km
    from agent.knowledge import wiki as wm
    topic = km.get_topic(ident) or km.get_topic_by_slug(ident)
    if not topic:
        return None
    by_url = {b["url"]: b for b in wm._load_bookmarks()}
    hydrated = []
    for url in topic.get("bookmark_urls") or []:
        b = by_url.get(url) or {}
        summary = b.get("summary") or b.get("notes") or ""
        hydrated.append({
            "url": url,
            "title": b.get("title") or url,
            "summary": summary,
            "saved_at": b.get("saved_at"),
        })
    topic["bookmarks"] = hydrated
    topic.pop("bookmark_urls", None)
    return topic


def _load_linkedin_calendar() -> dict:
    path = BASE_DIR / "skills" / "linkedin" / "workspace" / "content_calendar.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


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
