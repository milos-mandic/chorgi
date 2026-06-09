"""URL bookmarks — extract, fetch metadata, store, and digest."""

import html.parser
import json
import logging
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BOOKMARKS_FILE = Path(__file__).parent.parent / "skills" / "bookmarks" / "workspace" / "bookmarks.json"

sys.path.insert(0, str(Path(__file__).parent.parent / "skills"))
import _shared  # noqa: E402

# Match URLs starting with http(s)://
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def extract_urls(text: str) -> list[str]:
    """Return deduplicated list of URLs found in text."""
    seen = set()
    urls = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


class _MetaParser(html.parser.HTMLParser):
    """Minimal HTML parser to extract <title> and meta description."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.description = ""
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            attr_dict = dict(attrs)
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.description = attr_dict.get("content", "")

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join(self._title_parts).strip()

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)


def fetch_page_meta(url: str) -> dict:
    """Fetch page title and description via stdlib. Returns {url, title, description}."""
    result = {"url": url, "title": "", "description": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ChorgiBot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            # Read only first 32KB to avoid downloading huge pages
            raw = resp.read(32768)
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                text = raw.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                text = raw.decode("utf-8", errors="replace")

        parser = _MetaParser()
        parser.feed(text)
        result["title"] = parser.title[:200]
        result["description"] = parser.description[:500]
    except Exception as e:
        logger.debug(f"Failed to fetch metadata for {url}: {e}")
    return result


def load_bookmarks() -> list[dict]:
    """Return all bookmarks as a flat list (newest first by convention)."""
    data = _shared.load_json(BOOKMARKS_FILE, [], tolerant=True)
    # Tolerate the legacy {"bookmarks": [...]} shape during transition.
    if isinstance(data, dict) and isinstance(data.get("bookmarks"), list):
        return data["bookmarks"]
    if isinstance(data, list):
        return data
    return []


def save_bookmarks(bookmarks: list[dict]) -> None:
    _shared.save_json(BOOKMARKS_FILE, bookmarks)


def add_bookmark(
    url: str,
    title: str = "",
    summary: str = "",
    *,
    notes: str = "",
    tags: list[str] | None = None,
) -> int:
    """Insert or merge a bookmark. Returns count of unsent bookmarks."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _shared.file_lock(BOOKMARKS_FILE):
        bookmarks = load_bookmarks()
        for b in bookmarks:
            if b["url"] == url:
                if title:
                    b["title"] = title
                if summary:
                    b["summary"] = summary
                if notes:
                    b["notes"] = notes
                if tags:
                    b["tags"] = tags
                break
        else:
            bookmarks.insert(0, {
                "url": url,
                "title": title,
                "summary": summary,
                "notes": notes,
                "tags": tags or [],
                "saved_at": now,
                "emailed": False,
            })
        save_bookmarks(bookmarks)
    return sum(1 for b in bookmarks if not b.get("emailed"))


def get_unsent_bookmarks() -> list[dict]:
    """Return bookmarks where emailed == false."""
    return [b for b in load_bookmarks() if not b.get("emailed")]


def mark_emailed(urls: list[str]):
    """Set emailed = true for given URLs."""
    url_set = set(urls)
    with _shared.file_lock(BOOKMARKS_FILE):
        bookmarks = load_bookmarks()
        for b in bookmarks:
            if b["url"] in url_set:
                b["emailed"] = True
        save_bookmarks(bookmarks)
