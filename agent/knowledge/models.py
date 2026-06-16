"""Data access functions. Sub-agents call these via post_meeting_cli."""

import json
import sqlite3
from datetime import datetime, timezone

from . import content as content_mod
from .db import connect
from .ids import ulid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def _rows(rs) -> list[dict]:
    return [dict(r) for r in rs]


# ---- people ---------------------------------------------------------------

def _unique_slug(conn, base: str) -> str:
    slug = base
    n = 1
    while conn.execute("SELECT 1 FROM people WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def upsert_person(
    name: str,
    *,
    linkedin_url: str | None = None,
    email: str | None = None,
    x_handle: str | None = None,
    role: str | None = None,
    company: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> dict:
    """Insert a new person (no merge logic for M1). Returns the row."""
    conn = connect()
    try:
        slug = _unique_slug(conn, content_mod.slugify(name))
        pid = ulid()
        now = _now()
        notes_path = None
        if notes:
            p = content_mod.person_path(slug)
            content_mod.write_atomic(p, notes if notes.endswith("\n") else notes + "\n")
            notes_path = str(p)
        conn.execute(
            "INSERT INTO people(id, name, slug, linkedin_url, email, x_handle, "
            "role, company, tags, source, notes_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid, name, slug, linkedin_url, email, x_handle, role, company,
                json.dumps(tags or []), source, notes_path, now, now,
            ),
        )
        conn.commit()
        return _row(conn.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone())
    finally:
        conn.close()


def get_person(person_id: str) -> dict | None:
    conn = connect()
    try:
        return _row(conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone())
    finally:
        conn.close()


def list_people() -> list[dict]:
    conn = connect()
    try:
        return _rows(conn.execute("SELECT * FROM people ORDER BY name COLLATE NOCASE"))
    finally:
        conn.close()


def match_person(name: str | None = None, email: str | None = None) -> dict | None:
    """Find a person by exact email (preferred) or case-insensitive name."""
    conn = connect()
    try:
        if email:
            r = conn.execute(
                "SELECT * FROM people WHERE LOWER(email) = LOWER(?) LIMIT 1",
                (email,),
            ).fetchone()
            if r:
                return _row(r)
        if name:
            r = conn.execute(
                "SELECT * FROM people WHERE LOWER(name) = LOWER(?) LIMIT 1",
                (name,),
            ).fetchone()
            if r:
                return _row(r)
        return None
    finally:
        conn.close()


def update_person_fields(person_id: str, patch: dict) -> dict | None:
    """Apply a patch. Special key `notes_append`: append paragraph to person's notes file."""
    allowed = {"name", "linkedin_url", "email", "x_handle", "role", "company", "tags", "source"}
    sets, vals = [], []
    for k, v in patch.items():
        if k in allowed:
            if k == "tags" and isinstance(v, list):
                v = json.dumps(v)
            sets.append(f"{k} = ?")
            vals.append(v)
    conn = connect()
    try:
        cur = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        if not cur:
            return None
        if sets:
            sets.append("updated_at = ?")
            vals.append(_now())
            vals.append(person_id)
            conn.execute(f"UPDATE people SET {', '.join(sets)} WHERE id = ?", vals)
        notes_append = patch.get("notes_append")
        if notes_append:
            slug = cur["slug"]
            path = content_mod.person_path(slug)
            content_mod.append(path, f"\n{notes_append.rstrip()}\n")
            if not cur["notes_path"]:
                conn.execute(
                    "UPDATE people SET notes_path = ?, updated_at = ? WHERE id = ?",
                    (str(path), _now(), person_id),
                )
        conn.commit()
        return _row(conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone())
    finally:
        conn.close()


def delete_person(person_id: str) -> bool:
    """Delete a person. interaction_participants rows cascade (FK ON DELETE CASCADE)."""
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def person_interactions(person_id: str, limit: int = 20) -> list[dict]:
    conn = connect()
    try:
        return _rows(conn.execute(
            "SELECT i.* FROM interactions i "
            "JOIN interaction_participants p ON p.interaction_id = i.id "
            "WHERE p.person_id = ? "
            "ORDER BY COALESCE(i.occurred_at, i.created_at) DESC LIMIT ?",
            (person_id, limit),
        ))
    finally:
        conn.close()


# ---- interactions ---------------------------------------------------------

def create_interaction(
    *,
    interaction_id: str | None = None,
    type: str,
    title: str | None = None,
    occurred_at: str | None = None,
    summary: str | None = None,
    content_path: str | None = None,
    source: str | None = None,
    source_ref: str | None = None,
    participant_ids: list[str] | None = None,
) -> dict:
    iid = interaction_id or ulid()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO interactions(id, type, title, occurred_at, summary, "
            "content_path, source, source_ref, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iid, type, title, occurred_at, summary, content_path, source, source_ref, _now()),
        )
        for pid in participant_ids or []:
            conn.execute(
                "INSERT OR IGNORE INTO interaction_participants(interaction_id, person_id) "
                "VALUES (?, ?)",
                (iid, pid),
            )
        conn.commit()
        return _row(conn.execute("SELECT * FROM interactions WHERE id = ?", (iid,)).fetchone())
    finally:
        conn.close()


def link_participants(interaction_id: str, person_ids: list[str]) -> None:
    conn = connect()
    try:
        for pid in person_ids:
            conn.execute(
                "INSERT OR IGNORE INTO interaction_participants(interaction_id, person_id) "
                "VALUES (?, ?)",
                (interaction_id, pid),
            )
        conn.commit()
    finally:
        conn.close()


# ---- tasks ----------------------------------------------------------------

def create_task(
    *,
    title: str,
    description: str | None = None,
    status: str = "accepted",
    due_at: str | None = None,
    person_id: str | None = None,
    interaction_id: str | None = None,
    source: str = "manual",
) -> dict:
    tid = ulid()
    now = _now()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO tasks(id, title, description, status, due_at, person_id, "
            "interaction_id, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tid, title, description, status, due_at, person_id, interaction_id, source, now, now),
        )
        conn.commit()
        return _row(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())
    finally:
        conn.close()


def list_tasks_v2(status: str | None = None) -> list[dict]:
    conn = connect()
    try:
        if status:
            return _rows(conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY COALESCE(due_at, created_at)",
                (status,),
            ))
        return _rows(conn.execute("SELECT * FROM tasks ORDER BY created_at DESC"))
    finally:
        conn.close()


# ---- inbox ----------------------------------------------------------------

def create_inbox_item(
    *,
    type: str,
    payload: dict,
    source_interaction_id: str | None = None,
) -> dict:
    iid = ulid()
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO inbox_items(id, type, payload, source_interaction_id, "
            "status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (iid, type, json.dumps(payload), source_interaction_id, _now()),
        )
        conn.commit()
        return _row(conn.execute("SELECT * FROM inbox_items WHERE id = ?", (iid,)).fetchone())
    finally:
        conn.close()


def list_inbox(status: str = "pending") -> list[dict]:
    conn = connect()
    try:
        rows = _rows(conn.execute(
            "SELECT * FROM inbox_items WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ))
        for r in rows:
            try:
                r["payload"] = json.loads(r["payload"])
            except (TypeError, json.JSONDecodeError):
                pass
        return rows
    finally:
        conn.close()


def _get_inbox_item(conn, item_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM inbox_items WHERE id = ?", (item_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["payload"] = json.loads(d["payload"])
    except (TypeError, json.JSONDecodeError):
        pass
    return d


def accept_inbox_item(item_id: str) -> dict | None:
    """Apply the proposal and mark the item accepted."""
    conn = connect()
    try:
        item = _get_inbox_item(conn, item_id)
        if not item or item["status"] != "pending":
            return None
        payload = item["payload"] if isinstance(item["payload"], dict) else {}
        result: dict = {"item_id": item_id, "type": item["type"]}

        if item["type"] == "task_proposal":
            t = create_task(
                title=payload.get("title", "(untitled)"),
                description=payload.get("description"),
                status="accepted",
                due_at=payload.get("due_at"),
                person_id=payload.get("person_id"),
                interaction_id=item["source_interaction_id"] or payload.get("interaction_id"),
                source="agent_suggestion",
            )
            result["task"] = t

        elif item["type"] == "contact_update":
            person_id = payload.get("person_id")
            patch = payload.get("patch") or {}
            if person_id:
                updated = update_person_fields(person_id, patch)
                result["person"] = updated

        elif item["type"] == "new_person":
            fields = payload.get("fields") or {}
            person = upsert_person(
                name=fields.get("name", "Unknown"),
                linkedin_url=fields.get("linkedin_url"),
                email=fields.get("email"),
                x_handle=fields.get("x_handle"),
                role=fields.get("role"),
                company=fields.get("company"),
                tags=fields.get("tags"),
                source=fields.get("source"),
                notes=fields.get("notes"),
            )
            result["person"] = person
            if item["source_interaction_id"]:
                link_participants(item["source_interaction_id"], [person["id"]])

        conn.execute(
            "UPDATE inbox_items SET status = 'accepted', decided_at = ? WHERE id = ?",
            (_now(), item_id),
        )
        conn.commit()
        return result
    finally:
        conn.close()


def reject_inbox_item(item_id: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE inbox_items SET status = 'rejected', decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (_now(), item_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---- wiki -----------------------------------------------------------------

def _unique_wiki_slug(conn, base: str) -> str:
    slug = base
    n = 1
    while conn.execute("SELECT 1 FROM wiki_topics WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def list_topics() -> list[dict]:
    """Sidebar-friendly list — no article_md."""
    conn = connect()
    try:
        return _rows(conn.execute(
            "SELECT id, slug, title, summary, bookmark_count, article_built_at, "
            "article_dirty, created_at, updated_at "
            "FROM wiki_topics ORDER BY bookmark_count DESC, title COLLATE NOCASE"
        ))
    finally:
        conn.close()


def get_topic(topic_id: str) -> dict | None:
    conn = connect()
    try:
        topic = _row(conn.execute(
            "SELECT * FROM wiki_topics WHERE id = ?", (topic_id,)
        ).fetchone())
        if not topic:
            return None
        urls = [r["bookmark_url"] for r in conn.execute(
            "SELECT bookmark_url FROM wiki_topic_bookmarks "
            "WHERE topic_id = ? ORDER BY assigned_at",
            (topic_id,),
        )]
        topic["bookmark_urls"] = urls
        return topic
    finally:
        conn.close()


def get_topic_by_slug(slug: str) -> dict | None:
    conn = connect()
    try:
        r = conn.execute("SELECT id FROM wiki_topics WHERE slug = ?", (slug,)).fetchone()
        return get_topic(r["id"]) if r else None
    finally:
        conn.close()


def create_topic(title: str, summary: str | None = None, slug: str | None = None) -> dict:
    conn = connect()
    try:
        from . import content as content_mod
        base = slug or content_mod.slugify(title)
        s = _unique_wiki_slug(conn, base or "topic")
        tid = ulid()
        now = _now()
        conn.execute(
            "INSERT INTO wiki_topics(id, slug, title, summary, article_md, "
            "article_built_at, bookmark_count, article_dirty, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, NULL, 0, 1, ?, ?)",
            (tid, s, title, summary, now, now),
        )
        conn.commit()
        return _row(conn.execute("SELECT * FROM wiki_topics WHERE id = ?", (tid,)).fetchone())
    finally:
        conn.close()


def assign_bookmark_to_topic(topic_id: str, url: str, assigned_by: str = "assign") -> bool:
    """Returns True if newly assigned (False if already present)."""
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT 1 FROM wiki_topic_bookmarks WHERE topic_id = ? AND bookmark_url = ?",
            (topic_id, url),
        ).fetchone()
        if existing:
            return False
        now = _now()
        conn.execute(
            "INSERT INTO wiki_topic_bookmarks(topic_id, bookmark_url, assigned_at, assigned_by) "
            "VALUES (?, ?, ?, ?)",
            (topic_id, url, now, assigned_by),
        )
        conn.execute(
            "UPDATE wiki_topics SET bookmark_count = bookmark_count + 1, "
            "article_dirty = 1, updated_at = ? WHERE id = ?",
            (now, topic_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def remove_bookmark_from_topic(topic_id: str, url: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM wiki_topic_bookmarks WHERE topic_id = ? AND bookmark_url = ?",
            (topic_id, url),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "UPDATE wiki_topics SET bookmark_count = MAX(0, bookmark_count - 1), "
            "article_dirty = 1, updated_at = ? WHERE id = ?",
            (_now(), topic_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def topic_for_bookmark(url: str) -> dict | None:
    conn = connect()
    try:
        r = conn.execute(
            "SELECT t.* FROM wiki_topics t "
            "JOIN wiki_topic_bookmarks m ON m.topic_id = t.id "
            "WHERE m.bookmark_url = ? LIMIT 1",
            (url,),
        ).fetchone()
        return _row(r)
    finally:
        conn.close()


def set_article(topic_id: str, markdown: str) -> None:
    conn = connect()
    try:
        now = _now()
        conn.execute(
            "UPDATE wiki_topics SET article_md = ?, article_built_at = ?, "
            "article_dirty = 0, updated_at = ? WHERE id = ?",
            (markdown, now, now, topic_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_dirty_topics(limit: int = 10) -> list[dict]:
    conn = connect()
    try:
        return _rows(conn.execute(
            "SELECT id, slug, title, summary, bookmark_count FROM wiki_topics "
            "WHERE article_dirty = 1 AND bookmark_count > 0 "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ))
    finally:
        conn.close()


def replace_topic_bookmarks(topic_id: str, urls: list[str], assigned_by: str = "cluster") -> None:
    """Atomically replace the bookmark set for a topic. Updates count + dirty."""
    conn = connect()
    try:
        now = _now()
        conn.execute("DELETE FROM wiki_topic_bookmarks WHERE topic_id = ?", (topic_id,))
        for url in urls:
            conn.execute(
                "INSERT OR IGNORE INTO wiki_topic_bookmarks"
                "(topic_id, bookmark_url, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
                (topic_id, url, now, assigned_by),
            )
        conn.execute(
            "UPDATE wiki_topics SET bookmark_count = ?, article_dirty = 1, updated_at = ? "
            "WHERE id = ?",
            (len(urls), now, topic_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_topic(topic_id: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM wiki_topics WHERE id = ?", (topic_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
