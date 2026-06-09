"""Wiki: cluster bookmarks into topics and synthesize per-topic articles via Haiku."""

import asyncio
import json
import logging
import re

from agent.api_client import call_haiku
from agent import bookmarks as bookmarks_mod
from . import models

logger = logging.getLogger(__name__)

_assign_lock = asyncio.Lock()
_BATCH_SIZE = 80  # bookmarks per Haiku recluster call
_SUMMARY_TRUNCATE = 240


def _parse_json(raw: str):
    """Pull a JSON object/array out of raw Haiku text, tolerating fences."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _load_bookmarks() -> list[dict]:
    """All bookmarks from the unified store."""
    return bookmarks_mod.load_bookmarks()


def _bookmark_by_url(url: str) -> dict | None:
    for b in _load_bookmarks():
        if b["url"] == url:
            return b
    return None


def _short_summary(b: dict) -> str:
    s = (b.get("summary") or b.get("notes") or "").strip()
    if not s and b.get("tags"):
        s = "Tags: " + ", ".join(b["tags"])
    if len(s) > _SUMMARY_TRUNCATE:
        s = s[:_SUMMARY_TRUNCATE].rstrip() + "…"
    return s


# ---- single-bookmark fast path ---------------------------------------------

async def assign_or_create_topic(url: str, title: str, summary: str) -> str | None:
    """Pick a topic for this bookmark (existing or new). Returns topic_id or None."""
    async with _assign_lock:
        try:
            # Already assigned? leave it alone (recluster handles drift).
            existing = models.topic_for_bookmark(url)
            if existing:
                return existing["id"]

            topics = models.list_topics()
            short = _short_summary({"summary": summary})

            if not topics:
                # Bootstrap: just create a topic from this bookmark alone.
                t = await _topic_for_lone_bookmark(title, short)
                models.assign_bookmark_to_topic(t["id"], url, assigned_by="assign")
                return t["id"]

            choice = await _haiku_pick_topic(title, short, topics)
            if not choice:
                return None

            if choice.get("topic") == "new":
                new_title = (choice.get("title") or title or "Untitled").strip()[:120]
                new_summary = (choice.get("summary") or short or "").strip()[:240]
                t = models.create_topic(new_title, new_summary)
                models.assign_bookmark_to_topic(t["id"], url, assigned_by="assign")
                return t["id"]

            topic_id = choice.get("topic_id")
            if not any(t["id"] == topic_id for t in topics):
                return None
            models.assign_bookmark_to_topic(topic_id, url, assigned_by="assign")
            return topic_id
        except Exception as e:
            logger.exception("assign_or_create_topic failed for %s: %s", url, e)
            return None


async def _topic_for_lone_bookmark(title: str, summary: str) -> dict:
    """Ask Haiku to name a topic for a single bookmark (no existing topics yet)."""
    prompt = (
        "You are creating a wiki topic from a single bookmark. Output strict JSON:\n"
        '{"title": "<2-4 word topic name>", "summary": "<one-line summary of the topic>"}\n\n'
        f"Bookmark title: {title}\n"
        f"Bookmark summary: {summary}\n"
    )
    raw, _ = await call_haiku(system="", messages=[{"role": "user", "content": prompt}], max_tokens=200)
    parsed = _parse_json(raw) or {}
    t_title = (parsed.get("title") or title or "Untitled").strip()[:120]
    t_summary = (parsed.get("summary") or summary or "").strip()[:240]
    return models.create_topic(t_title, t_summary)


async def _haiku_pick_topic(title: str, summary: str, topics: list[dict]) -> dict | None:
    topic_lines = "\n".join(
        f'- id={t["id"]} | {t["title"]}: {(t.get("summary") or "")[:120]}'
        for t in topics
    )
    prompt = (
        "Place a new bookmark into the best existing wiki topic, or create a new one.\n"
        "Output strict JSON. If reusing an existing topic:\n"
        '  {"topic": "existing", "topic_id": "<id from list>"}\n'
        "If none fit (the bookmark is on a clearly different subject), create a new one:\n"
        '  {"topic": "new", "title": "<2-4 word topic name>", "summary": "<one-line summary>"}\n\n'
        "Existing topics:\n"
        f"{topic_lines}\n\n"
        "New bookmark:\n"
        f"Title: {title}\n"
        f"Summary: {summary}\n"
    )
    raw, _ = await call_haiku(system="", messages=[{"role": "user", "content": prompt}], max_tokens=250)
    return _parse_json(raw)


# ---- full re-cluster --------------------------------------------------------

async def recluster_all() -> dict:
    """Reassign every bookmark to a (possibly new) topic. Returns stats."""
    async with _assign_lock:
        all_bookmarks = _load_bookmarks()
        if not all_bookmarks:
            # Empty: drop all topics
            for t in models.list_topics():
                models.delete_topic(t["id"])
            return {"bookmarks": 0, "topics": 0, "deleted": 0}

        existing_topic_titles: list[str] = []  # carry across batches
        # topic_title (lowercase) -> list[urls]
        proposed: dict[str, list[str]] = {}
        # topic_title -> human-cased title
        cased: dict[str, str] = {}
        # topic_title -> summary
        summaries: dict[str, str] = {}

        for batch_start in range(0, len(all_bookmarks), _BATCH_SIZE):
            batch = all_bookmarks[batch_start : batch_start + _BATCH_SIZE]
            clusters = await _haiku_cluster_batch(batch, existing_topic_titles)
            if not clusters:
                continue
            for c in clusters:
                t_title = (c.get("title") or "").strip()
                if not t_title:
                    continue
                key = t_title.lower()
                cased.setdefault(key, t_title[:120])
                if c.get("summary") and key not in summaries:
                    summaries[key] = str(c["summary"]).strip()[:240]
                urls = c.get("urls") or []
                if not isinstance(urls, list):
                    continue
                proposed.setdefault(key, []).extend(u for u in urls if isinstance(u, str))
            existing_topic_titles = list(cased.values())

        # Dedup URLs per topic (last writer wins to avoid double-counts)
        seen_urls: set[str] = set()
        finalized: dict[str, list[str]] = {}
        for key, urls in proposed.items():
            kept = []
            for u in urls:
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                kept.append(u)
            if kept:
                finalized[key] = kept

        # Diff against current topics by lowercase title match
        current = {t["title"].lower(): t for t in models.list_topics()}
        deleted = 0
        topics_touched = 0

        for key, urls in finalized.items():
            topic_row = current.pop(key, None)
            if topic_row is None:
                topic_row = models.create_topic(cased[key], summaries.get(key))
            models.replace_topic_bookmarks(topic_row["id"], urls, assigned_by="cluster")
            topics_touched += 1

        # Anything not chosen → delete
        for leftover in current.values():
            models.delete_topic(leftover["id"])
            deleted += 1

        return {
            "bookmarks": len(all_bookmarks),
            "topics": topics_touched,
            "deleted": deleted,
        }


async def _haiku_cluster_batch(batch: list[dict], existing_titles: list[str]) -> list[dict]:
    items = []
    for b in batch:
        items.append({
            "url": b["url"],
            "title": (b.get("title") or "")[:160],
            "summary": _short_summary(b),
        })
    existing_block = ""
    if existing_titles:
        existing_block = (
            "Existing topics from earlier batches (extend them when bookmarks fit; "
            "otherwise add new ones):\n"
            + "\n".join(f"- {t}" for t in existing_titles[:40])
            + "\n\n"
        )
    prompt = (
        "Cluster these bookmarks into wiki topics. Output strict JSON: a list of "
        "topic objects with title (2-5 words), summary (one line), and urls (array). "
        "Every bookmark URL must appear in exactly one topic. Prefer fewer, broader "
        "topics over many tiny ones; merge near-duplicates.\n\n"
        f"{existing_block}"
        "Bookmarks:\n"
        + json.dumps(items, indent=2)
        + "\n\nOutput JSON only."
    )
    raw, _ = await call_haiku(
        system="",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,
    )
    parsed = _parse_json(raw)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("topics"), list):
        return parsed["topics"]
    return []


# ---- article synthesis ------------------------------------------------------

async def resynthesize_topic(topic_id: str) -> bool:
    """Regenerate the markdown article for a topic. Returns True on success."""
    topic = models.get_topic(topic_id)
    if not topic:
        return False
    urls = topic.get("bookmark_urls") or []
    if not urls:
        models.delete_topic(topic_id)
        return False

    sources = []
    for u in urls:
        b = _bookmark_by_url(u)
        if not b:
            continue
        sources.append({
            "title": b.get("title") or u,
            "summary": _short_summary(b),
            "url": u,
        })
    if not sources:
        models.delete_topic(topic_id)
        return False

    prompt = (
        f"Write a concise wiki article (~150-400 words) in markdown that synthesizes "
        f"what these sources collectively say about \"{topic['title']}\". "
        "Use short sections with `##` headings when helpful. Do not list the URLs — "
        "they are rendered separately. Output the markdown only, no preamble.\n\n"
        "Topic summary: "
        f"{topic.get('summary') or '(none)'}\n\n"
        "Sources:\n"
        + "\n".join(
            f"- {s['title']}\n  {s['summary']}" for s in sources
        )
    )
    raw, _ = await call_haiku(
        system="",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )
    models.set_article(topic_id, raw.strip())
    return True


async def sweep_unassigned(limit: int = 20) -> int:
    """Find bookmarks not in any topic and assign them. Returns count assigned."""
    from .db import connect
    conn = connect()
    try:
        assigned = {r["bookmark_url"] for r in conn.execute(
            "SELECT bookmark_url FROM wiki_topic_bookmarks"
        )}
    finally:
        conn.close()
    todo = [b for b in _load_bookmarks() if b["url"] not in assigned]
    if not todo:
        return 0
    done = 0
    for b in todo[:limit]:
        try:
            tid = await assign_or_create_topic(
                b["url"], b.get("title") or b["url"], b.get("summary") or "",
            )
            if tid:
                done += 1
        except Exception as e:
            logger.warning("sweep: assign failed for %s: %s", b["url"], e)
    return done


async def drain_dirty(limit: int = 10) -> int:
    """Resynthesize up to `limit` dirty topics. Returns count done."""
    done = 0
    for t in models.list_dirty_topics(limit=limit):
        try:
            ok = await resynthesize_topic(t["id"])
            if ok:
                done += 1
        except Exception as e:
            logger.warning("resynthesize_topic(%s) failed: %s", t["id"], e)
    return done


async def run_maintenance() -> dict:
    """Schedule entrypoint: full recluster then drain dirty articles."""
    stats = await recluster_all()
    stats["resynthesized"] = await drain_dirty(limit=10)
    return stats
