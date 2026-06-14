"""Watch list — detect watchable links, enrich (title/image/summary/rating/duration), store.

Parallel to agent/bookmarks.py but tuned for video/film. Data lives in
skills/watchlist/workspace/watchlist.json so the skill CLI and the dashboard API
share one store (same arrangement as bookmarks).
"""

import html.parser
import json
import logging
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

WATCHLIST_FILE = Path(__file__).parent.parent / "skills" / "watchlist" / "workspace" / "watchlist.json"

sys.path.insert(0, str(Path(__file__).parent.parent / "skills"))
import _shared  # noqa: E402

# Reuse the generic URL extractor from bookmarks so behaviour stays identical.
from agent.bookmarks import extract_urls  # noqa: E402,F401

# Host substrings that mark a link as "something to watch". Substring match keeps
# subdomains (www., m., open.) working without per-host special-casing.
WATCH_DOMAINS = (
    "youtube.com", "youtu.be", "vimeo.com", "imdb.com", "twitch.tv",
    "ted.com", "netflix.com", "tv.apple.com", "primevideo.com",
    "hulu.com", "disneyplus.com", "max.com",
)

# Message intent that forces watch routing even for an unknown domain.
_WATCH_INTENT_RE = re.compile(r"\bwatch(list|\s*later)?\b", re.IGNORECASE)


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_watchable(url: str) -> bool:
    """True when the URL points at a known video/film/TV host."""
    host = _host(url)
    return any(d in host for d in WATCH_DOMAINS)


def has_watch_intent(text: str) -> bool:
    """True when the message itself asks to watch something (override for odd domains)."""
    return bool(_WATCH_INTENT_RE.search(text or ""))


def source_of(url: str) -> str:
    """Short label for the card/icon, e.g. 'youtube', 'imdb', 'vimeo'."""
    host = _host(url)
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    for d in ("vimeo", "imdb", "twitch", "ted", "netflix", "hulu", "max"):
        if d in host:
            return d
    if "tv.apple.com" in host:
        return "apple tv"
    if "primevideo.com" in host:
        return "prime video"
    if "disneyplus.com" in host:
        return "disney+"
    # Fall back to the registrable-ish host without a leading www.
    return host[4:] if host.startswith("www.") else host


# ---------------------------------------------------------------------------
# Metadata enrichment (stdlib + oEmbed, no API keys)
# ---------------------------------------------------------------------------

class _WatchMetaParser(html.parser.HTMLParser):
    """Pull <title>, og:title/og:image/og:description, and any ld+json blocks."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.og_title = ""
        self.og_image = ""
        self.description = ""
        self.ld_blocks: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_ld = False
        self._ld_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = dict(attrs)
            name = (a.get("name") or "").lower()
            prop = (a.get("property") or "").lower()
            content = a.get("content") or ""
            if prop == "og:title" and not self.og_title:
                self.og_title = content
            elif prop == "og:image" and not self.og_image:
                self.og_image = content
            elif name == "description" or prop == "og:description":
                if not self.description:
                    self.description = content
        elif tag == "script":
            if (dict(attrs).get("type") or "").lower() == "application/ld+json":
                self._in_ld = True
                self._ld_parts = []

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join(self._title_parts).strip()
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld_blocks.append("".join(self._ld_parts))

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        if self._in_ld:
            self._ld_parts.append(data)


_DURATION_RE = re.compile(
    r"P(?:\d+Y)?(?:\d+M)?(?:\d+W)?(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
)


def format_duration(iso: str) -> str:
    """Turn an ISO-8601 duration ('PT1H23M45S') into '1h 23m'. Returns '' if unparseable."""
    if not iso:
        return ""
    m = _DURATION_RE.fullmatch(iso.strip())
    if not m:
        return ""
    h, mins, secs = (int(x) if x else 0 for x in m.groups())
    parts = []
    if h:
        parts.append(f"{h}h")
    if mins:
        parts.append(f"{mins}m")
    if not parts and secs:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _walk_ld(node):
    """Yield every dict in a parsed JSON-LD blob (handles lists and @graph)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_ld(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_ld(item)


def _ld_rating_and_duration(blocks: list[str]) -> tuple[str, str]:
    """Best-effort pull of aggregateRating.ratingValue + ISO duration from ld+json."""
    rating, duration = "", ""
    for raw in blocks:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for obj in _walk_ld(data):
            if not isinstance(obj, dict):
                continue
            if not rating:
                agg = obj.get("aggregateRating")
                if isinstance(agg, dict) and agg.get("ratingValue") is not None:
                    rating = str(agg["ratingValue"])
            if not duration and isinstance(obj.get("duration"), str):
                duration = format_duration(obj["duration"])
        if rating and duration:
            break
    return rating, duration


# A browser-like UA gets real HTML from sites that 403/202 a bot string.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _fetch(url: str, timeout: int = 6) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(65536)  # cap; ld+json sits late on some pages
        charset = resp.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


_IMDB_ID_RE = re.compile(r"/title/(tt\d+)")


def _imdb_meta(url: str) -> dict:
    """IMDB title pages 202 a scraper, so use the keyless suggestion API.

    Returns {title, image, year} or {} when the URL isn't an IMDB title or the
    lookup fails.
    """
    m = _IMDB_ID_RE.search(url)
    if not m:
        return {}
    tt = m.group(1)
    try:
        body = _fetch(f"https://v2.sg.media-imdb.com/suggestion/t/{tt}.json")
        data = json.loads(body)
    except Exception as e:
        logger.debug("IMDB suggestion lookup failed for %s: %s", url, e)
        return {}
    for item in data.get("d", []):
        if item.get("id") == tt and item.get("l"):
            return {
                "title": item["l"],
                "image": (item.get("i") or {}).get("imageUrl", ""),
                "year": str(item.get("y") or ""),
            }
    return {}


def _oembed(url: str) -> dict:
    """Clean title + thumbnail from YouTube/Vimeo oEmbed. Returns {} on miss."""
    host = _host(url)
    if "youtube.com" in host or "youtu.be" in host:
        endpoint = "https://www.youtube.com/oembed"
    elif "vimeo.com" in host:
        endpoint = "https://vimeo.com/api/oembed.json"
    else:
        return {}
    try:
        q = urllib.parse.urlencode({"url": url, "format": "json"})
        body = _fetch(f"{endpoint}?{q}")
        return json.loads(body)
    except Exception as e:
        logger.debug("oEmbed failed for %s: %s", url, e)
        return {}


def fetch_watch_meta(url: str) -> dict:
    """Return {title, image, description, rating, duration} via stdlib + oEmbed."""
    result = {"url": url, "title": "", "image": "", "description": "",
              "rating": "", "duration": ""}

    # IMDB blocks page scraping (202 + empty body); the keyless suggestion API
    # still returns title + poster. Use it and skip the HTML fetch.
    if "imdb.com" in _host(url):
        imdb = _imdb_meta(url)
        if imdb:
            result["title"] = imdb["title"][:200]
            result["image"] = imdb["image"]
            if imdb.get("year"):
                result["description"] = f"IMDB title ({imdb['year']})"
        return result

    # og / ld+json from the page itself.
    try:
        text = _fetch(url)
        parser = _WatchMetaParser()
        parser.feed(text)
        result["title"] = (parser.og_title or parser.title)[:200]
        result["image"] = parser.og_image
        result["description"] = parser.description[:500]
        rating, duration = _ld_rating_and_duration(parser.ld_blocks)
        result["rating"] = rating
        result["duration"] = duration
    except Exception as e:
        logger.debug("Failed to fetch watch metadata for %s: %s", url, e)

    # oEmbed wins for title/thumbnail when available (cleaner than og on YouTube).
    o = _oembed(url)
    if o.get("title"):
        result["title"] = o["title"][:200]
    if o.get("thumbnail_url"):
        result["image"] = o["thumbnail_url"]
    if not result["duration"] and o.get("duration"):
        # Vimeo oEmbed returns seconds as an int.
        try:
            secs = int(o["duration"])
            result["duration"] = format_duration(f"PT{secs}S") if secs < 60 \
                else format_duration(f"PT{secs // 3600}H{(secs % 3600) // 60}M")
        except (TypeError, ValueError):
            pass
    return result


# ---------------------------------------------------------------------------
# Storage (locked JSON, shared with the skill CLI + dashboard API)
# ---------------------------------------------------------------------------

def load_watch_items() -> list[dict]:
    """Return all watch items as a flat list (newest first by convention)."""
    data = _shared.load_json(WATCHLIST_FILE, [], tolerant=True)
    if isinstance(data, dict) and isinstance(data.get("watchlist"), list):
        return data["watchlist"]
    return data if isinstance(data, list) else []


def save_watch_items(items: list[dict]) -> None:
    _shared.save_json(WATCHLIST_FILE, items)


def add_watch_item(
    url: str,
    title: str = "",
    summary: str = "",
    *,
    image: str = "",
    rating: str = "",
    duration: str = "",
    source: str = "",
    notes: str = "",
    tags: list[str] | None = None,
    where_url: str = "",
) -> int:
    """Insert or merge a watch item. Returns count of unwatched items."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _shared.file_lock(WATCHLIST_FILE):
        items = load_watch_items()
        for it in items:
            if it["url"] == url:
                if title:
                    it["title"] = title
                if summary:
                    it["summary"] = summary
                if image:
                    it["image"] = image
                if rating:
                    it["rating"] = rating
                if duration:
                    it["duration"] = duration
                if notes:
                    it["notes"] = notes
                if tags:
                    it["tags"] = tags
                if where_url:
                    it["where_url"] = where_url
                    it["where_source"] = source_of(where_url)
                break
        else:
            items.insert(0, {
                "url": url,
                "title": title,
                "summary": summary,
                "image": image,
                "source": source or source_of(url),
                "rating": rating,
                "duration": duration,
                "notes": notes,
                "tags": tags or [],
                "where_url": where_url,
                "where_source": source_of(where_url) if where_url else "",
                "saved_at": now,
                "watched": False,
            })
        save_watch_items(items)
    return sum(1 for it in items if not it.get("watched"))


def set_where(url: str, where_url: str) -> bool:
    """Attach (or clear) a 'where to watch' link on an item. Returns True if found."""
    with _shared.file_lock(WATCHLIST_FILE):
        items = load_watch_items()
        for it in items:
            if it["url"] == url:
                it["where_url"] = where_url
                it["where_source"] = source_of(where_url) if where_url else ""
                save_watch_items(items)
                return True
    return False


def set_watched(url: str, watched: bool) -> bool:
    """Toggle the watched flag for one item. Returns True if found."""
    with _shared.file_lock(WATCHLIST_FILE):
        items = load_watch_items()
        for it in items:
            if it["url"] == url:
                it["watched"] = bool(watched)
                save_watch_items(items)
                return True
    return False
