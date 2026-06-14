"""Dashboard API handlers — pure (status, payload) functions behind the webhook server.

The HTTP plumbing (sockets, headers, SSE streaming, Fathom webhooks) lives in
agent/webhook.py; everything routable as JSON-in/JSON-out lives here so the
two can evolve separately. State helpers are importable so the UI shares the
skill CLIs' write path.
"""

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

# Lazy-loaded skill modules
_task_cli = None
_bookmarks_cli = None
_watchlist_mod = None
_shopping_mod = None
_local_chat = None

# Single lock for all JSON mutations (tasks + bookmarks).
# These files are tiny; a coarse lock keeps things simple and safe.
# skills/_shared.file_lock adds cross-process exclusion on top (skill CLIs
# running as sub-agent subprocesses mutate the same files).
_data_lock = threading.Lock()

sys.path.insert(0, str(BASE_DIR / "skills"))
import _shared  # noqa: E402


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


def _get_watchlist():
    global _watchlist_mod
    if _watchlist_mod is None:
        from agent import watchlist as wl
        _watchlist_mod = wl
    return _watchlist_mod


def _get_shopping():
    global _shopping_mod
    if _shopping_mod is None:
        from agent import shopping as sl
        _shopping_mod = sl
    return _shopping_mod


def get_local_chat():
    global _local_chat
    if _local_chat is None:
        from agent import local_chat as lc
        _local_chat = lc
    return _local_chat


# ---------------------------------------------------------------------------
# Route dispatch
# ---------------------------------------------------------------------------

def api_get(path: str) -> tuple[int, dict]:
    """Handle a GET /api/... request. Returns (status, payload)."""
    try:
        if path == "/api/state":
            return 200, _build_state()
        if path == "/api/tasks":
            tc = _get_task_cli()
            return 200, {"tasks": tc.load_tasks()}
        if path == "/api/bookmarks":
            bc = _get_bookmarks_cli()
            return 200, {"bookmarks": bc.load_bookmarks()}
        if path == "/api/watchlist":
            wl = _get_watchlist()
            return 200, {"watchlist": wl.load_watch_items()}
        if path == "/api/shopping":
            sl = _get_shopping()
            return 200, {"shopping": sl.load_items()}
        if path == "/api/linkedin/calendar":
            return 200, _load_linkedin_calendar()
        if path == "/api/people":
            from agent.knowledge import models as km
            return 200, {"people": km.list_people()}
        if path.startswith("/api/people/"):
            pid = path[len("/api/people/"):]
            from agent.knowledge import models as km
            person = km.get_person(pid)
            if not person:
                return 404, {"error": "person not found"}
            person["interactions"] = km.person_interactions(pid)
            return 200, person
        if path == "/api/inbox":
            from agent.knowledge import models as km
            return 200, {"inbox": km.list_inbox()}
        if path == "/api/wiki/topics":
            from agent.knowledge import models as km
            return 200, {"topics": km.list_topics()}
        if path.startswith("/api/wiki/topics/"):
            ident = path[len("/api/wiki/topics/"):]
            topic = _get_wiki_topic(ident)
            if topic is None:
                return 404, {"error": "topic not found"}
            return 200, topic
        if path == "/api/chat/config":
            return 200, get_local_chat().config()
        if path == "/api/chat/conversations":
            lc = get_local_chat()
            return 200, {"conversations": lc.list_conversations()}
        if path.startswith("/api/chat/conversations/"):
            cid = path[len("/api/chat/conversations/"):]
            conv = get_local_chat().get_conversation(cid)
            if conv is None:
                return 404, {"error": "conversation not found"}
            return 200, conv
        return 404, {"error": "not found"}
    except Exception as e:
        logger.error("API GET %s failed: %s", path, e, exc_info=True)
        return 500, {"error": str(e)}


def api_write(path: str, method: str, body: dict | None, server) -> tuple[int, dict]:
    """Handle a POST/PATCH/DELETE /api/... request. Returns (status, payload).

    `server` is the WebhookServer instance — used only by routes that bridge
    to the async side (_trigger_skill / _trigger_wiki).
    """
    try:
        # Tasks
        if path == "/api/tasks" and method == "POST":
            if body is None:
                return 400, {"error": "bad json"}
            return 200, _create_task(body)

        if path.startswith("/api/tasks/"):
            task_id = path[len("/api/tasks/"):]
            if method == "PATCH":
                if body is None:
                    return 400, {"error": "bad json"}
                updated = _update_task(task_id, body)
                if updated is None:
                    return 404, {"error": "task not found"}
                return 200, updated
            if method == "DELETE":
                ok = _delete_task(task_id)
                return (200 if ok else 404), {"deleted": ok}

        # Bookmarks
        if path == "/api/bookmarks" and method == "POST":
            if body is None:
                return 400, {"error": "bad json"}
            return 200, _create_bookmark(body)

        if path == "/api/bookmarks" and method == "DELETE":
            if body is None or not body.get("url"):
                return 400, {"error": "url required"}
            ok = _delete_bookmark(body["url"])
            return (200 if ok else 404), {"deleted": ok}

        # Watch list
        if path == "/api/watchlist" and method == "POST":
            if body is None:
                return 400, {"error": "bad json"}
            return 200, _create_watch_item(body)

        if path == "/api/watchlist" and method == "PATCH":
            if body is None or not body.get("url"):
                return 400, {"error": "url required"}
            wl = _get_watchlist()
            if "where_url" in body:
                ok = wl.set_where(body["url"], (body.get("where_url") or "").strip())
            else:
                ok = wl.set_watched(body["url"], bool(body.get("watched")))
            return (200 if ok else 404), {"updated": ok}

        if path == "/api/watchlist" and method == "DELETE":
            if body is None or not body.get("url"):
                return 400, {"error": "url required"}
            ok = _delete_watch_item(body["url"])
            return (200 if ok else 404), {"deleted": ok}

        # Shopping list
        if path == "/api/shopping" and method == "POST":
            if body is None:
                return 400, {"error": "bad json"}
            return 200, _create_shopping_item(body)

        if path == "/api/shopping" and method == "PATCH":
            if body is None or not body.get("url"):
                return 400, {"error": "url required"}
            sl = _get_shopping()
            if "amount" in body or "currency" in body:
                ok = sl.set_price(body["url"], sl.parse_amount(body.get("amount")),
                                  (body.get("currency") or "").strip())
            else:
                ok = sl.set_bought(body["url"], bool(body.get("bought")))
            return (200 if ok else 404), {"updated": ok}

        if path == "/api/shopping" and method == "DELETE":
            if body is None or not body.get("url"):
                return 400, {"error": "url required"}
            ok = _delete_shopping_item(body["url"])
            return (200 if ok else 404), {"deleted": ok}

        # Inbox accept / reject
        if path.startswith("/api/inbox/") and method == "POST":
            rest = path[len("/api/inbox/"):]
            item_id, _, action = rest.partition("/")
            from agent.knowledge import models as km
            if action == "accept":
                res = km.accept_inbox_item(item_id)
                if res is None:
                    return 404, {"error": "item not pending or missing"}
                return 200, res
            if action == "reject":
                ok = km.reject_inbox_item(item_id)
                return (200 if ok else 404), {"rejected": ok}

        # Wiki
        if path == "/api/wiki/recluster" and method == "POST":
            queued, reason = server._trigger_wiki("recluster", None)
            return 200, {"queued": queued, "reason": reason}

        if path.startswith("/api/wiki/topics/") and method == "POST":
            rest = path[len("/api/wiki/topics/"):]
            topic_id, _, action = rest.partition("/")
            if action == "resynthesize":
                queued, reason = server._trigger_wiki("resynthesize", topic_id)
                return 200, {"queued": queued, "reason": reason,
                             "topic_id": topic_id}

        if path.startswith("/api/wiki/topics/") and method == "DELETE":
            topic_id = path[len("/api/wiki/topics/"):]
            from agent.knowledge import models as km
            ok = km.delete_topic(topic_id)
            return (200 if ok else 404), {"deleted": ok}

        # Local chat — create / delete conversations.
        # (The streaming message send is handled in webhook.py's do_POST.)
        if path == "/api/chat/conversations" and method == "POST":
            return 200, get_local_chat().create_conversation()

        if path.startswith("/api/chat/conversations/") and method == "DELETE":
            cid = path[len("/api/chat/conversations/"):]
            ok = get_local_chat().delete_conversation(cid)
            return (200 if ok else 404), {"deleted": ok}

        # Trigger subagent
        if path == "/api/trigger" and method == "POST":
            if body is None:
                return 400, {"error": "bad json"}
            skill = body.get("skill")
            task = body.get("task")
            if not skill or not task:
                return 400, {"error": "skill and task required"}
            server._trigger_skill(skill, task)
            return 200, {"status": "queued", "skill": skill}

        return 404, {"error": "not found"}
    except Exception as e:
        logger.error("API %s %s failed: %s", method, path, e, exc_info=True)
        return 500, {"error": str(e)}


# ---------------------------------------------------------------------------
# State + mutation helpers (importable so the UI shares the CLI write path)
# ---------------------------------------------------------------------------

def _build_state() -> dict:
    tc = _get_task_cli()
    bc = _get_bookmarks_cli()
    wl = _get_watchlist()
    sl = _get_shopping()
    with _data_lock:
        tasks = tc.load_tasks()
        bookmarks = bc.load_bookmarks()
        watchlist = wl.load_watch_items()
        shopping = sl.load_items()
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
        "watchlist": watchlist,
        "shopping": shopping,
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
    with _data_lock, _shared.file_lock(tc.DATA_FILE):
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
            "time_class": body.get("time_class") or "anytime",
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
                # Keep pending but remember the requested time (mirrors task_cli).
                task["requested_at"] = scheduled_at
                task["_calendar_warning"] = result.get("error", "calendar create failed")
        tasks.insert(0, task)
        tc.save_tasks(tasks)
    return task


_TASK_FIELDS = {"title", "notes", "priority", "estimated_minutes",
                "deadline", "tags", "status", "carry_count",
                "scheduled_at", "calendar_event_id", "time_class"}


def _update_task(task_id: str, body: dict) -> dict | None:
    tc = _get_task_cli()
    with _data_lock, _shared.file_lock(tc.DATA_FILE):
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
    with _data_lock, _shared.file_lock(tc.DATA_FILE):
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
    with _data_lock, _shared.file_lock(bc.DATA_FILE):
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
    with _data_lock, _shared.file_lock(bc.DATA_FILE):
        bookmarks = bc.load_bookmarks()
        before = len(bookmarks)
        bookmarks = [b for b in bookmarks if b["url"] != url]
        if len(bookmarks) == before:
            return False
        bc.save_bookmarks(bookmarks)
    return True


def _create_watch_item(body: dict) -> dict:
    wl = _get_watchlist()
    url = (body.get("url") or "").strip()
    if not url:
        return {"error": "url required"}
    tags = body.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    # Best-effort enrichment for manual adds (title/image/rating/duration).
    meta = {}
    if not body.get("title") or not body.get("image"):
        try:
            meta = wl.fetch_watch_meta(url)
        except Exception as e:
            logger.warning("Watch enrichment failed for %s: %s", url, e)
    with _data_lock:
        wl.add_watch_item(
            url,
            title=(body.get("title") or meta.get("title") or "").strip(),
            summary=(body.get("notes") or meta.get("description") or "").strip(),
            image=meta.get("image", ""),
            rating=meta.get("rating", ""),
            duration=meta.get("duration", ""),
            source=wl.source_of(url),
            notes=(body.get("notes") or "").strip(),
            tags=tags,
            where_url=(body.get("where_url") or "").strip(),
        )
        for it in wl.load_watch_items():
            if it["url"] == url:
                return it
    return {"url": url}


def _delete_watch_item(url: str) -> bool:
    wl = _get_watchlist()
    with _data_lock, _shared.file_lock(wl.WATCHLIST_FILE):
        items = wl.load_watch_items()
        before = len(items)
        items = [it for it in items if it["url"] != url]
        if len(items) == before:
            return False
        wl.save_watch_items(items)
    return True


def _create_shopping_item(body: dict) -> dict:
    """Create a shopping item, auto-filling title/image/price/category from the link.

    Enrichment policy (caller values win, blanks scraped/categorized) lives in
    agent.shopping.add_from_url so the dashboard and the skill CLI stay identical.
    """
    sl = _get_shopping()
    url = (body.get("url") or "").strip()
    if not url:
        return {"error": "url required"}
    user_tags = body.get("tags") or []
    if isinstance(user_tags, str):
        user_tags = [t.strip() for t in user_tags.split(",") if t.strip()]
    with _data_lock:
        return sl.add_from_url(
            url,
            title=(body.get("title") or "").strip(),
            amount=sl.parse_amount(body.get("amount")),
            currency=(body.get("currency") or "").strip(),
            notes=(body.get("notes") or "").strip(),
            tags=user_tags or None,
            image=(body.get("image") or "").strip(),
        )


def _delete_shopping_item(url: str) -> bool:
    sl = _get_shopping()
    with _data_lock, _shared.file_lock(sl.SHOPPING_FILE):
        items = sl.load_items()
        before = len(items)
        items = [it for it in items if it["url"] != url]
        if len(items) == before:
            return False
        sl.save_items(items)
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
