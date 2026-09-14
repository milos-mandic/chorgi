#!/usr/bin/env python3
"""Social posts board: LinkedIn posts and Substack Notes, one per platform per day.

Storage is workspace/posts.json ({"posts": [...]}) plus image files in
workspace/images/. The dashboard (agent/api_handlers.py) imports this module and
calls the same functions as the CLI, so both share one write path; every
mutation holds _shared.file_lock(DATA_FILE) across its load → modify → save.

The batch-import contract is schema/social_posts.v1.schema.json. validate_doc()
enforces it without a jsonschema dependency and reads its enums and limits from
that file, so the schema Claude is given and the checks that run can't drift.

Post text is stored exactly as received — never trimmed or normalized — so what
is copied back out is byte-for-byte what went in.
"""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
DATA_FILE = SKILL_DIR / "workspace" / "posts.json"
IMAGES_DIR = SKILL_DIR / "workspace" / "images"
SCHEMA_FILE = SKILL_DIR / "schema" / "social_posts.v1.schema.json"

sys.path.insert(0, str(SKILL_DIR.parent))
import _shared  # noqa: E402

PLATFORM_LABELS = {"linkedin": "LinkedIn", "substack_note": "Substack Note"}

# A nudge fires once the scheduled time has passed, but never for a post more
# than this far in the past — a restart or a back-dated import must not spam.
NUDGE_WINDOW = timedelta(hours=12)

_IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
_IMAGE_NAME_RE = re.compile(r"^sp_[A-Za-z0-9_]+\.(png|jpg|webp|gif)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SocialError(Exception):
    """A request the board refuses. `status` is the HTTP code the API returns."""

    def __init__(self, message: str, status: int = 400, **details):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details

    def payload(self) -> dict:
        return {"error": self.message, **self.details}


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_FILE.read_text())


@lru_cache(maxsize=1)
def rules() -> dict:
    """Enums and limits read out of the schema file — its single source of truth."""
    schema = load_schema()
    post = schema["$defs"]["post"]
    props = post["properties"]
    image_props = props["image"]["properties"]
    text_max_by_platform = {}
    for clause in post.get("allOf", []):
        platform = clause["if"]["properties"]["platform"]["const"]
        text_max_by_platform[platform] = clause["then"]["properties"]["text"]["maxLength"]
    posts = schema["properties"]["posts"]
    return {
        "top_fields": set(schema["properties"]),
        "version": schema["properties"]["version"]["const"],
        "min_posts": posts["minItems"],
        "max_posts": posts["maxItems"],
        "post_fields": set(props),
        "required": list(post["required"]),
        "platforms": list(props["platform"]["enum"]),
        "statuses": list(props["status"]["enum"]),
        "default_status": props["status"]["default"],
        "scheduled_at_pattern": re.compile(props["scheduled_at"]["pattern"]),
        "text_max": props["text"]["maxLength"],
        "text_max_by_platform": text_max_by_platform,
        "notes_max": props["notes"]["maxLength"],
        "image_fields": set(image_props),
        "data_uri_pattern": re.compile(image_props["data_uri"]["pattern"]),
        "image_max_bytes": image_props["data_uri"]["x-maxDecodedBytes"],
        "alt_max": image_props["alt"]["maxLength"],
    }


def _platform_error(value) -> str | None:
    platforms = rules()["platforms"]
    if value not in platforms:
        return f"platform must be one of: {', '.join(platforms)}"
    return None


def _status_error(value) -> str | None:
    statuses = rules()["statuses"]
    if value not in statuses:
        return f"status must be one of: {', '.join(statuses)}"
    return None


def _text_error(text, platform) -> str | None:
    r = rules()
    if not isinstance(text, str):
        return "text must be a string"
    if not text.strip():
        return "text is empty"
    limit = r["text_max_by_platform"].get(platform, r["text_max"])
    if len(text) > limit:
        label = PLATFORM_LABELS.get(platform, "a post")
        return f"text is {len(text)} characters; {label} allows at most {limit}"
    return None


def _string_error(value, field: str, limit: int) -> str | None:
    if not isinstance(value, str):
        return f"{field} must be a string"
    if len(value) > limit:
        return f"{field} is {len(value)} characters; at most {limit} allowed"
    return None


def parse_when(value) -> datetime:
    """A scheduled_at input → aware Europe/Berlin datetime. Raises SocialError."""
    if not isinstance(value, str) or not rules()["scheduled_at_pattern"].search(value):
        raise SocialError("scheduled_at must look like 'YYYY-MM-DD HH:MM' (Europe/Berlin time)")
    try:
        return _shared.parse_local_datetime(value)
    except ValueError:
        raise SocialError(f"scheduled_at {value!r} is not a real date and time")


def _sniff_image(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


def decode_data_uri(data_uri) -> tuple[bytes, str]:
    """Decode an image data URI → (bytes, extension). Raises SocialError."""
    r = rules()
    if not isinstance(data_uri, str) or not r["data_uri_pattern"].search(data_uri):
        raise SocialError("image must be a data URI like 'data:image/png;base64,…' "
                          "(PNG, JPEG, WebP or GIF)")
    head, _, payload = data_uri.partition(",")
    declared_mime = head[len("data:"):-len(";base64")]
    max_mb = r["image_max_bytes"] // (1024 * 1024)
    if len(payload) * 3 // 4 > r["image_max_bytes"] + 2:
        raise SocialError(f"image is larger than {max_mb} MB")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise SocialError("image data is not valid base64")
    if len(raw) > r["image_max_bytes"]:
        raise SocialError(f"image is larger than {max_mb} MB")
    actual = _sniff_image(raw)
    if actual is None:
        raise SocialError("image data is not a PNG, JPEG, WebP or GIF")
    if _IMAGE_MIME[actual] != declared_mime:
        raise SocialError(f"image is declared as {declared_mime} but the data is {_IMAGE_MIME[actual]}")
    return raw, actual


def validate_doc(doc) -> list[dict]:
    """Check an import document against the v1 contract.

    Returns a list of {index, path, message}; empty means valid. `index` is the
    post's position in `posts`, or None for a document-level problem. Beyond what
    JSON Schema can express, this also rejects whitespace-only text, two posts for
    the same platform and day, base64 that doesn't decode, and image bytes that
    don't match the declared type.
    """
    r = rules()
    errors: list[dict] = []

    def add(index, path, message):
        errors.append({"index": index, "path": path, "message": message})

    if not isinstance(doc, dict):
        add(None, "$", "the document must be a JSON object")
        return errors
    for key in doc:
        if key not in r["top_fields"]:
            add(None, f"$.{key}", f"unknown field '{key}' (allowed: {', '.join(sorted(r['top_fields']))})")
    if "version" not in doc:
        add(None, "$.version", "version is required")
    elif type(doc["version"]) is not int or doc["version"] != r["version"]:
        add(None, "$.version", f"version must be {r['version']}")
    posts = doc.get("posts")
    if not isinstance(posts, list):
        add(None, "$.posts", "posts must be an array")
        return errors
    if len(posts) < r["min_posts"]:
        add(None, "$.posts", f"posts must contain at least {r['min_posts']} post")
    if len(posts) > r["max_posts"]:
        add(None, "$.posts", f"posts can contain at most {r['max_posts']} posts")

    seen: dict[tuple[str, str], int] = {}
    for i, item in enumerate(posts):
        base = f"$.posts[{i}]"
        if not isinstance(item, dict):
            add(i, base, "each post must be an object")
            continue
        for key in item:
            if key not in r["post_fields"]:
                add(i, f"{base}.{key}", f"unknown field '{key}' (allowed: {', '.join(sorted(r['post_fields']))})")
        for key in r["required"]:
            if key not in item:
                add(i, f"{base}.{key}", f"{key} is required")

        platform = item.get("platform")
        platform_ok = "platform" in item and _platform_error(platform) is None
        if "platform" in item and not platform_ok:
            add(i, f"{base}.platform", _platform_error(platform))

        when = None
        if "scheduled_at" in item:
            try:
                when = parse_when(item["scheduled_at"])
            except SocialError as e:
                add(i, f"{base}.scheduled_at", e.message)

        if "text" in item:
            msg = _text_error(item["text"], platform if platform_ok else None)
            if msg:
                add(i, f"{base}.text", msg)
        if "status" in item:
            msg = _status_error(item["status"])
            if msg:
                add(i, f"{base}.status", msg)
        if "notes" in item:
            msg = _string_error(item["notes"], "notes", r["notes_max"])
            if msg:
                add(i, f"{base}.notes", msg)
        if "image" in item:
            image = item["image"]
            if not isinstance(image, dict):
                add(i, f"{base}.image", "image must be an object with a data_uri")
            else:
                for key in image:
                    if key not in r["image_fields"]:
                        add(i, f"{base}.image.{key}", f"unknown field '{key}' (allowed: {', '.join(sorted(r['image_fields']))})")
                if "data_uri" not in image:
                    add(i, f"{base}.image.data_uri", "image.data_uri is required")
                else:
                    try:
                        decode_data_uri(image["data_uri"])
                    except SocialError as e:
                        add(i, f"{base}.image.data_uri", e.message)
                if "alt" in image:
                    msg = _string_error(image["alt"], "image.alt", r["alt_max"])
                    if msg:
                        add(i, f"{base}.image.alt", msg)

        if platform_ok and when is not None:
            slot = (platform, when.date().isoformat())
            if slot in seen:
                add(i, f"{base}.scheduled_at",
                    f"posts[{seen[slot]}] is already the {PLATFORM_LABELS[platform]} post for "
                    f"{slot[1]} — one post per platform per day")
            else:
                seen[slot] = i
    return errors


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def load_posts() -> list[dict]:
    return _shared.load_json(DATA_FILE, {"posts": []}).get("posts", [])


@contextmanager
def _locked_posts():
    """Read-modify-write the post list under the cross-process lock.

    Raising inside the block skips the save, so a refused change never
    half-applies.
    """
    with _shared.locked_json(DATA_FILE, {"posts": []}) as data:
        yield data.setdefault("posts", [])


def find_post(posts: list[dict], post_id: str) -> dict | None:
    for post in posts:
        if post["id"] == post_id:
            return post
    return None


def _require(posts: list[dict], post_id) -> dict:
    post = find_post(posts, post_id) if isinstance(post_id, str) else None
    if post is None:
        raise SocialError("post not found", 404)
    return post


def post_date(post: dict) -> str:
    # scheduled_at is Berlin-local ISO with offset, so the date is literal.
    return post["scheduled_at"][:10]


def _occupant(posts: list[dict], platform: str, day: str, exclude_id: str | None = None) -> dict | None:
    for post in posts:
        if post["id"] != exclude_id and post["platform"] == platform and post_date(post) == day:
            return post
    return None


def _slot_taken(platform: str, day: str, other: dict) -> SocialError:
    return SocialError(f"{PLATFORM_LABELS[platform]} already has a post on {day}", 409,
                       conflict_id=other["id"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(posts: list[dict]) -> str:
    taken = {p["id"] for p in posts}
    while True:
        post_id = f"sp_{int(time.time())}_{secrets.token_hex(3)}"
        if post_id not in taken:
            return post_id


def image_path(name) -> Path | None:
    """Filesystem path for a stored image name, or None if the name isn't one of ours."""
    if not isinstance(name, str) or not _IMAGE_NAME_RE.match(name):
        return None
    return IMAGES_DIR / name


def image_mime(name: str) -> str:
    return _IMAGE_MIME.get(name.rsplit(".", 1)[-1], "application/octet-stream")


def _write_image(post_id: str, raw: bytes, ext: str) -> str:
    # Content-hashed name: the dashboard serves images with a long cache lifetime.
    name = f"{post_id}_{hashlib.sha256(raw).hexdigest()[:10]}.{ext}"
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    path = IMAGES_DIR / name
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    return name


def _unlink_image(image: dict | None) -> None:
    path = image_path((image or {}).get("file"))
    if path is not None:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Mutations (shared by the CLI and the dashboard API)
# ---------------------------------------------------------------------------

def create_post(fields: dict) -> dict:
    if not isinstance(fields, dict):
        raise SocialError("expected a JSON object")
    r = rules()
    platform = fields.get("platform")
    if _platform_error(platform):
        raise SocialError(_platform_error(platform))
    when = parse_when(fields.get("scheduled_at"))
    text = fields.get("text")
    status = fields.get("status") or r["default_status"]
    notes = fields.get("notes") or ""
    for msg in (_text_error(text, platform), _status_error(status),
                _string_error(notes, "notes", r["notes_max"])):
        if msg:
            raise SocialError(msg)

    with _locked_posts() as posts:
        day = when.date().isoformat()
        other = _occupant(posts, platform, day)
        if other:
            raise _slot_taken(platform, day, other)
        now = _now_iso()
        post = {
            "id": _new_id(posts),
            "platform": platform,
            "scheduled_at": when.isoformat(),
            "text": text,
            "status": status,
            "notes": notes,
            "image": None,
            "created_at": now,
            "updated_at": now,
            "posted_at": now if status == "posted" else None,
            "nudged_at": None,
        }
        posts.append(post)
    return post


def update_post(post_id: str, fields: dict) -> dict:
    """Patch platform, scheduled_at, text, status, notes and/or image_alt."""
    if not isinstance(fields, dict):
        raise SocialError("expected a JSON object")
    r = rules()
    with _locked_posts() as posts:
        post = _require(posts, post_id)

        platform = fields.get("platform", post["platform"])
        if _platform_error(platform):
            raise SocialError(_platform_error(platform))
        scheduled_at = post["scheduled_at"]
        if "scheduled_at" in fields:
            new = parse_when(fields["scheduled_at"]).isoformat()
            # The editor's datetime-local input drops seconds, so a save that
            # doesn't move the post by at least a minute keeps the stored value
            # (and with it the nudge state).
            if new[:16] != scheduled_at[:16]:
                scheduled_at = new
        text = fields.get("text", post["text"])
        status = fields.get("status", post["status"])
        notes = fields.get("notes", post.get("notes", "")) or ""
        for msg in (_text_error(text, platform), _status_error(status),
                    _string_error(notes, "notes", r["notes_max"])):
            if msg:
                raise SocialError(msg)
        alt = None
        if "image_alt" in fields and post.get("image"):
            alt = fields["image_alt"] or ""
            msg = _string_error(alt, "image_alt", r["alt_max"])
            if msg:
                raise SocialError(msg)

        day = scheduled_at[:10]
        if (platform, day) != (post["platform"], post_date(post)):
            other = _occupant(posts, platform, day, exclude_id=post["id"])
            if other:
                raise _slot_taken(platform, day, other)

        if scheduled_at != post["scheduled_at"]:
            post["nudged_at"] = None
        if status != post["status"]:
            post["posted_at"] = _now_iso() if status == "posted" else None
        post.update(platform=platform, scheduled_at=scheduled_at, text=text,
                    status=status, notes=notes, updated_at=_now_iso())
        if alt is not None:
            post["image"]["alt"] = alt
    return post


def delete_post(post_id: str) -> None:
    with _locked_posts() as posts:
        post = _require(posts, post_id)
        posts.remove(post)
    _unlink_image(post.get("image"))


def _relocate(post: dict, platform: str, day: str) -> None:
    msg = _text_error(post["text"], platform)
    if msg:
        raise SocialError(f"can't move to {PLATFORM_LABELS[platform]}: {msg}")
    # Rebuilt through the parser so the UTC offset is right on the new day (DST).
    post["scheduled_at"] = _shared.parse_local_datetime(
        f"{day} {post['scheduled_at'][11:19]}").isoformat()
    post["platform"] = platform
    post["nudged_at"] = None
    post["updated_at"] = _now_iso()


def move_post(post_id: str, platform: str, day: str) -> dict:
    """Move a post to another cell, keeping its time of day.

    If the cell already holds a post, the two swap cells (each keeps its own
    time), so a drop never destroys anything.
    """
    if _platform_error(platform):
        raise SocialError(_platform_error(platform))
    if not isinstance(day, str) or not _DATE_RE.match(day):
        raise SocialError("date must be YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise SocialError(f"{day} is not a real date")

    with _locked_posts() as posts:
        post = _require(posts, post_id)
        old_platform, old_day = post["platform"], post_date(post)
        if (platform, day) == (old_platform, old_day):
            return {"post": post, "swapped": None}
        other = _occupant(posts, platform, day, exclude_id=post["id"])
        _relocate(post, platform, day)
        if other:
            _relocate(other, old_platform, old_day)
    return {"post": post, "swapped": other}


def set_image(post_id: str, data_uri, alt=None) -> dict:
    raw, ext = decode_data_uri(data_uri)
    alt = alt or ""
    msg = _string_error(alt, "alt", rules()["alt_max"])
    if msg:
        raise SocialError(msg)
    with _locked_posts() as posts:
        post = _require(posts, post_id)
        old = post.get("image")
        name = _write_image(post["id"], raw, ext)
        post["image"] = {"file": name, "alt": alt}
        post["updated_at"] = _now_iso()
    # Re-uploading identical bytes yields the same name — don't delete the new file.
    if old and old.get("file") != name:
        _unlink_image(old)
    return post


def remove_image(post_id: str) -> dict:
    with _locked_posts() as posts:
        post = _require(posts, post_id)
        old = post.get("image")
        post["image"] = None
        post["updated_at"] = _now_iso()
    _unlink_image(old)
    return post


# ---------------------------------------------------------------------------
# Batch import
# ---------------------------------------------------------------------------

def preview_import(doc, overwrite: bool = False, now: datetime | None = None) -> dict:
    """What importing `doc` would do, without saving anything.

    Each row's action is "new", "replace" (slot taken, overwrite on), "conflict"
    (slot taken, overwrite off) or "error". `ok` means apply_import would succeed.
    """
    r = rules()
    errors = validate_doc(doc)
    items = doc.get("posts") if isinstance(doc, dict) else None
    items = items if isinstance(items, list) else []
    existing = load_posts()
    now = now or datetime.now(timezone.utc)

    rows = []
    counts = {"new": 0, "replace": 0, "conflict": 0, "error": 0}
    for i, item in enumerate(items):
        item = item if isinstance(item, dict) else {}
        text = item.get("text")
        scheduled_at = item.get("scheduled_at")
        platform = item.get("platform")
        row = {
            "index": i,
            "platform": platform if isinstance(platform, str) else None,
            "scheduled_at": scheduled_at if isinstance(scheduled_at, str) else None,
            "date": None,
            "time": None,
            "snippet": text[:200] if isinstance(text, str) else "",
            "status": item.get("status", r["default_status"]),
            "has_image": "image" in item,
            "errors": [e["message"] for e in errors if e["index"] == i],
            "warnings": [],
            "action": "error",
        }
        if not row["errors"]:
            when = _shared.parse_local_datetime(scheduled_at)
            row["date"] = when.date().isoformat()
            row["time"] = when.strftime("%H:%M")
            other = _occupant(existing, platform, row["date"])
            if other:
                row["action"] = "replace" if overwrite else "conflict"
                row["existing_id"] = other["id"]
                row["existing_snippet"] = other["text"][:200]
                if other.get("status") == "posted":
                    row["warnings"].append("the post already there is marked posted")
            else:
                row["action"] = "new"
            if when < now:
                row["warnings"].append("scheduled time is in the past")
        counts[row["action"]] += 1
        rows.append(row)

    return {
        "ok": not errors and counts["conflict"] == 0,
        "counts": counts,
        "rows": rows,
        "errors": [e for e in errors if e["index"] is None],
    }


def apply_import(doc, overwrite: bool = False) -> dict:
    """Validate and save every post in `doc` — all or nothing."""
    errors = validate_doc(doc)
    if errors:
        raise SocialError(f"the import has {len(errors)} validation error(s)", 400, errors=errors)
    r = rules()
    prepared = []
    for item in doc["posts"]:
        when = _shared.parse_local_datetime(item["scheduled_at"])
        image = decode_data_uri(item["image"]["data_uri"]) if "image" in item else None
        prepared.append((item, when, image))

    created: list[dict] = []
    replaced: list[dict] = []
    with _locked_posts() as posts:
        conflicts = []
        for item, when, _ in prepared:
            other = _occupant(posts, item["platform"], when.date().isoformat())
            if other:
                conflicts.append({"platform": item["platform"],
                                  "date": when.date().isoformat(),
                                  "existing_id": other["id"]})
        if conflicts and not overwrite:
            raise SocialError(f"{len(conflicts)} slot(s) already have a post — "
                              "import with overwrite to replace them", 409, conflicts=conflicts)
        conflict_ids = {c["existing_id"] for c in conflicts}
        replaced = [p for p in posts if p["id"] in conflict_ids]
        for post in replaced:
            posts.remove(post)

        now = _now_iso()
        written = []
        try:
            for item, when, image in prepared:
                status = item.get("status", r["default_status"])
                post = {
                    "id": _new_id(posts),
                    "platform": item["platform"],
                    "scheduled_at": when.isoformat(),
                    "text": item["text"],
                    "status": status,
                    "notes": item.get("notes", ""),
                    "image": None,
                    "created_at": now,
                    "updated_at": now,
                    "posted_at": now if status == "posted" else None,
                    "nudged_at": None,
                }
                if image:
                    name = _write_image(post["id"], *image)
                    written.append(name)
                    post["image"] = {"file": name, "alt": item["image"].get("alt", "")}
                posts.append(post)
                created.append(post)
        except Exception:
            for name in written:
                image_path(name).unlink(missing_ok=True)
            raise

    for post in replaced:
        _unlink_image(post.get("image"))
    return {
        "imported": len(created),
        "replaced": len(replaced),
        "first_date": min(post_date(p) for p in created),
        "posts": created,
    }


# ---------------------------------------------------------------------------
# Telegram nudges (driven by the scheduler heartbeat)
# ---------------------------------------------------------------------------

def due_for_nudge(now: datetime | None = None) -> list[dict]:
    """Unposted, un-nudged posts whose time has come within the last NUDGE_WINDOW."""
    now = now or datetime.now(timezone.utc)
    due = []
    for post in load_posts():
        if post.get("status") == "posted" or post.get("nudged_at"):
            continue
        try:
            when = datetime.fromisoformat(post["scheduled_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - NUDGE_WINDOW <= when <= now:
            due.append(post)
    return sorted(due, key=lambda p: datetime.fromisoformat(p["scheduled_at"]))


def mark_nudged(post_id: str, now: datetime | None = None) -> None:
    with _locked_posts() as posts:
        post = find_post(posts, post_id)
        if post is not None:
            post["nudged_at"] = (now or datetime.now(timezone.utc)).isoformat()


def nudge_header(post: dict) -> str:
    label = PLATFORM_LABELS.get(post["platform"], post["platform"])
    lines = [f"📣 {label} post due at {post['scheduled_at'][11:16]} — the text follows, copy it as-is."]
    if post.get("status") == "draft":
        lines.append("⚠️ Still marked as a draft.")
    if post.get("image"):
        lines.append("🖼 The image comes after the text.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_doc(path: str):
    raw = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SocialError(f"not valid JSON: {e}")


def _first_line(text: str, width: int) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[0].strip()[:width] if lines else ""


def _print_preview(preview: dict) -> None:
    for e in preview["errors"]:
        print(f"✗ {e['path']}: {e['message']}")
    for row in preview["rows"]:
        label = PLATFORM_LABELS.get(row["platform"], str(row["platform"]))
        when = f"{row['date']} {row['time']}" if row["date"] else str(row["scheduled_at"])
        print(f"[{row['action']:>8}] posts[{row['index']}]  {when:<16}  {label:<13}  "
              f"{_first_line(row['snippet'], 60)}")
        for msg in row["errors"]:
            print(f"            ✗ {msg}")
        for msg in row["warnings"]:
            print(f"            ! {msg}")
    c = preview["counts"]
    print(f"\n{c['new']} new, {c['replace']} to replace, {c['conflict']} conflicting, "
          f"{c['error']} with errors")


def cmd_schema(args) -> int:
    print(json.dumps(load_schema(), indent=2, ensure_ascii=False))
    return 0


def cmd_validate(args) -> int:
    doc = _read_doc(args.file)
    errors = validate_doc(doc)
    if errors:
        for e in errors:
            print(f"✗ {e['path']}: {e['message']}")
        return 1
    print(f"✓ valid — {len(doc['posts'])} post(s)")
    return 0


def cmd_import(args) -> int:
    doc = _read_doc(args.file)
    preview = preview_import(doc, overwrite=args.overwrite)
    _print_preview(preview)
    if args.dry_run:
        return 0 if preview["ok"] else 1
    if not preview["ok"]:
        hint = ""
        if preview["counts"]["conflict"] and not preview["counts"]["error"] and not preview["errors"]:
            hint = " Pass --overwrite to replace the posts already in those slots."
        print("\nNothing imported." + hint)
        return 1
    result = apply_import(doc, overwrite=args.overwrite)
    replaced = f", replaced {result['replaced']}" if result["replaced"] else ""
    print(f"\nImported {result['imported']} post(s){replaced}.")
    return 0


def cmd_list(args) -> int:
    posts = load_posts()
    if not args.all:
        try:
            anchor = date.fromisoformat(args.week) if args.week else _shared.now_local().date()
        except ValueError:
            raise SocialError("--week must be a date like 2026-09-21")
        monday = anchor - timedelta(days=anchor.weekday())
        sunday = monday + timedelta(days=6)
        posts = [p for p in posts if monday.isoformat() <= post_date(p) <= sunday.isoformat()]
        print(f"Week of {monday.isoformat()}")
    if not posts:
        print("No posts.")
        return 0
    for p in sorted(posts, key=lambda p: (post_date(p), p["scheduled_at"][11:16], p["platform"])):
        day = date.fromisoformat(post_date(p))
        image = " [image]" if p.get("image") else ""
        print(f"{day.isoformat()} {day.strftime('%a')} {p['scheduled_at'][11:16]}  "
              f"{PLATFORM_LABELS.get(p['platform'], p['platform']):<13}  {p['status']:<6}  "
              f"{p['id']}{image}  {_first_line(p['text'], 70)}")
    return 0


def cmd_get(args) -> int:
    print(json.dumps(_require(load_posts(), args.id), indent=2, ensure_ascii=False))
    return 0


def cmd_set_status(args) -> int:
    post = update_post(args.id, {"status": args.status})
    print(f"{post['id']} → {post['status']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="social_cli.py",
        description="Social posts board — LinkedIn posts and Substack Notes, one per platform per day",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("schema", help="Print the import JSON Schema (the contract)")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("validate", help="Check an import file against the contract")
    p.add_argument("file", help="Path to the JSON file, or - for stdin")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("import", help="Import posts from a JSON file")
    p.add_argument("file", help="Path to the JSON file, or - for stdin")
    p.add_argument("--overwrite", action="store_true", help="Replace posts already in the same slots")
    p.add_argument("--dry-run", action="store_true", help="Show what would happen without saving")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("list", help="List posts for a week (default: this week)")
    p.add_argument("--week", help="Any date in the week to show (YYYY-MM-DD)")
    p.add_argument("--all", action="store_true", help="List every post")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get", help="Print one post as JSON (exact text)")
    p.add_argument("id")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("set-status", help="Change a post's status")
    p.add_argument("id")
    p.add_argument("status", choices=rules()["statuses"])
    p.set_defaults(func=cmd_set_status)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SocialError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        for err in e.details.get("errors", []):
            print(f"  ✗ {err['path']}: {err['message']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
