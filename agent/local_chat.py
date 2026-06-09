"""Local LLM chat — stdlib-only client + JSON persistence for the dashboard Chat tab.

Talks to a local OpenAI-compatible server (e.g. an MLX server on
http://localhost:8000/v1). Pure passthrough chat: no tools, no skills, no
Telegram. Conversations are stored one file per chat under
.personal/local_chat/ so the history sidebar can list them cheaply.

Config (env, loaded from .personal/secrets.env by agent/main.py):
  LOCAL_LLM_BASE_URL   e.g. http://localhost:8000/v1   (required to enable)
  LOCAL_LLM_MODEL      e.g. Qwen3.5-9B-OptiQ-4bit       (required to enable)
  LOCAL_LLM_API_KEY    optional; local servers usually ignore it
"""

import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BASE_DIR = Path(__file__).parent.parent
CHAT_DIR = BASE_DIR / ".personal" / "local_chat"

# Local chat files are independent of the tasks/bookmarks store, so they get
# their own lock rather than sharing webhook._data_lock.
_lock = threading.Lock()

# How long to wait for the local server. Generation can be slow on first token
# (model load), so keep this generous.
_STREAM_TIMEOUT = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conv_path(cid: str) -> Path:
    # cid is server-generated (uuid hex); still guard against path traversal.
    safe = "".join(c for c in cid if c.isalnum())
    return CHAT_DIR / f"{safe}.json"


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _write(conv: dict) -> None:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    path = _conv_path(conv["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(conv, ensure_ascii=False, indent=2))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def config() -> dict:
    base_url = (os.environ.get("LOCAL_LLM_BASE_URL") or "").rstrip("/")
    model = os.environ.get("LOCAL_LLM_MODEL") or ""
    return {
        "base_url": base_url,
        "model": model,
        "configured": bool(base_url and model),
    }


# ---------------------------------------------------------------------------
# Conversation storage
# ---------------------------------------------------------------------------

def list_conversations() -> list[dict]:
    """Return lightweight metadata for every conversation, newest first."""
    if not CHAT_DIR.exists():
        return []
    out = []
    for path in CHAT_DIR.glob("*.json"):
        conv = _read(path)
        if not conv:
            continue
        out.append({
            "id": conv.get("id"),
            "title": conv.get("title") or "New chat",
            "updated_at": conv.get("updated_at"),
            "message_count": len(conv.get("messages") or []),
        })
    out.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return out


def get_conversation(cid: str) -> dict | None:
    with _lock:
        return _read(_conv_path(cid))


def create_conversation() -> dict:
    with _lock:
        conv = {
            "id": uuid4().hex[:12],
            "title": "",
            "model": config()["model"],
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
        }
        _write(conv)
        return conv


def delete_conversation(cid: str) -> bool:
    with _lock:
        path = _conv_path(cid)
        if not path.exists():
            return False
        path.unlink()
        return True


def append_message(cid: str, role: str, content: str) -> dict | None:
    """Append a message, create the conversation if missing, derive a title."""
    with _lock:
        path = _conv_path(cid)
        conv = _read(path)
        if conv is None:
            conv = {
                "id": "".join(c for c in cid if c.isalnum()) or uuid4().hex[:12],
                "title": "",
                "model": config()["model"],
                "created_at": _now(),
                "messages": [],
            }
        conv.setdefault("messages", [])
        conv["messages"].append({"role": role, "content": content, "ts": _now()})
        if not conv.get("title") and role == "user":
            conv["title"] = content.strip().replace("\n", " ")[:50] or "New chat"
        conv["updated_at"] = _now()
        _write(conv)
        return conv


# ---------------------------------------------------------------------------
# Streaming completion
# ---------------------------------------------------------------------------

def stream_completion(messages: list[dict], model: str | None = None):
    """Yield text deltas from the local OpenAI-compatible /chat/completions.

    `messages` is a list of {"role", "content"} dicts. Raises RuntimeError with
    a readable message on connection/HTTP failure (mirrors api_client).
    """
    cfg = config()
    if not cfg["configured"]:
        raise RuntimeError(
            "Local LLM not configured — set LOCAL_LLM_BASE_URL and "
            "LOCAL_LLM_MODEL in .personal/secrets.env"
        )

    payload = {
        "model": model or cfg["model"],
        "messages": messages,
        "stream": True,
    }
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("LOCAL_LLM_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        f"{cfg['base_url']}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=_STREAM_TIMEOUT)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Local LLM HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Local LLM connection error: {e.reason}") from e

    with resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content")
            if delta:
                yield delta
