"""URL bookmarks — extract, fetch metadata, store, and digest."""

import html.parser
import json
import logging
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BOOKMARKS_FILE = Path(__file__).parent.parent / "skills" / "bookmarks" / "bookmarks.json"

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


def _load_data() -> dict:
    if BOOKMARKS_FILE.exists():
        try:
            return json.loads(BOOKMARKS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"bookmarks": []}


def _save_data(data: dict):
    BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_FILE.write_text(json.dumps(data, indent=2) + "\n")


def add_bookmark(url: str, title: str, summary: str) -> int:
    """Append a bookmark. Returns count of unsent bookmarks."""
    data = _load_data()
    # Avoid duplicates
    for b in data["bookmarks"]:
        if b["url"] == url:
            b["title"] = title
            b["summary"] = summary
            _save_data(data)
            return sum(1 for b in data["bookmarks"] if not b.get("emailed"))

    data["bookmarks"].append({
        "url": url,
        "title": title,
        "summary": summary,
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emailed": False,
    })
    _save_data(data)
    return sum(1 for b in data["bookmarks"] if not b.get("emailed"))


def get_unsent_bookmarks() -> list[dict]:
    """Return bookmarks where emailed == false."""
    data = _load_data()
    return [b for b in data["bookmarks"] if not b.get("emailed")]


def mark_emailed(urls: list[str]):
    """Set emailed = true for given URLs."""
    data = _load_data()
    url_set = set(urls)
    for b in data["bookmarks"]:
        if b["url"] in url_set:
            b["emailed"] = True
    _save_data(data)
