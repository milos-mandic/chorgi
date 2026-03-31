#!/usr/bin/env python3
"""CLI wrapper for research briefing operations. Used by the research sub-agent via Bash."""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).parent
TOPICS_FILE = SKILL_DIR / "topics.json"
WORKSPACE = SKILL_DIR / "workspace"
BRIEFING_DRAFT = WORKSPACE / "briefing_draft.json"
SENT_HISTORY = WORKSPACE / "sent_history.json"

# Add email skill to path for send_html_email
_EMAIL_SKILL_DIR = SKILL_DIR.parent / "email"
if str(_EMAIL_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_EMAIL_SKILL_DIR))


def _load_data():
    """Load topics.json."""
    return json.loads(TOPICS_FILE.read_text())


def _save_data(data):
    """Save topics.json."""
    TOPICS_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _load_sent_history():
    """Load sent article URLs from history file."""
    if SENT_HISTORY.exists():
        return json.loads(SENT_HISTORY.read_text())
    return {"sent_urls": [], "last_sent": None}


def _save_sent_history(history):
    """Save sent history. Keep only last 500 URLs to avoid unbounded growth."""
    history["sent_urls"] = history["sent_urls"][-500:]
    SENT_HISTORY.write_text(json.dumps(history, indent=2) + "\n")


# --- Topic commands ---

def cmd_topics_list(args):
    data = _load_data()
    print(json.dumps({"topics": data["topics"], "sources": data["sources"]}, indent=2))


def cmd_topics_add(args):
    data = _load_data()
    name_lower = args.name.lower()
    for t in data["topics"]:
        if t["name"].lower() == name_lower:
            print(f"Topic already exists: {t['name']}")
            sys.exit(1)
    topic = {"name": args.name, "context": args.context or ""}
    data["topics"].append(topic)
    _save_data(data)
    print(f"Added topic: {args.name}")


def cmd_topics_remove(args):
    data = _load_data()
    name_lower = args.name.lower()
    original_len = len(data["topics"])
    data["topics"] = [t for t in data["topics"] if t["name"].lower() != name_lower]
    if len(data["topics"]) == original_len:
        print(f"Topic not found: {args.name}")
        sys.exit(1)
    _save_data(data)
    print(f"Removed topic: {args.name}")


# --- Source commands ---

def cmd_sources_list(args):
    data = _load_data()
    print(json.dumps(data["sources"], indent=2))


def cmd_sources_add(args):
    data = _load_data()
    name_lower = args.name.lower()
    for s in data["sources"]:
        if s.lower() == name_lower:
            print(f"Source already exists: {s}")
            sys.exit(1)
    data["sources"].append(args.name)
    _save_data(data)
    print(f"Added source: {args.name}")


def cmd_sources_remove(args):
    data = _load_data()
    name_lower = args.name.lower()
    original_len = len(data["sources"])
    data["sources"] = [s for s in data["sources"] if s.lower() != name_lower]
    if len(data["sources"]) == original_len:
        print(f"Source not found: {args.name}")
        sys.exit(1)
    _save_data(data)
    print(f"Removed source: {args.name}")


# --- Send briefing ---

def _is_within_24h(date_str):
    """Check if a date string (e.g. 'Mar 28, 2026') is within the last 24 hours."""
    try:
        article_date = datetime.strptime(date_str.strip(), "%b %d, %Y")
        cutoff = datetime.now() - timedelta(hours=24)
        return article_date >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    except (ValueError, AttributeError):
        return False


def cmd_send_briefing(args):
    """Read briefing_draft.json, deduplicate, filter to last 24h, format as HTML, send via email."""
    if not BRIEFING_DRAFT.exists():
        print("No briefing draft found at workspace/briefing_draft.json")
        sys.exit(1)

    user_email = os.environ.get("USER_EMAIL", "")
    if not user_email:
        print("USER_EMAIL env var not set")
        sys.exit(1)

    draft = json.loads(BRIEFING_DRAFT.read_text())
    history = _load_sent_history()
    sent_urls = set(history["sent_urls"])

    # Filter out previously sent articles and articles older than 24h
    new_urls = []
    filtered_topics = []
    for topic in draft.get("topics", []):
        new_articles = [
            a for a in topic.get("articles", [])
            if a.get("url") not in sent_urls and _is_within_24h(a.get("date", ""))
        ]
        if new_articles:
            filtered_topics.append({"name": topic["name"], "articles": new_articles})
            new_urls.extend(a["url"] for a in new_articles)

    if not filtered_topics:
        print("No fresh articles to send (none from the last 24 hours).")
        return

    draft["topics"] = filtered_topics
    today = datetime.now().strftime("%B %d, %Y")
    subject = f"Research Briefing \u2014 {today}"

    html = _build_html(draft, today)

    import email_client
    result = email_client.send_html_email(user_email, subject, html)
    print(result)

    # Record sent URLs
    history["sent_urls"].extend(new_urls)
    history["last_sent"] = datetime.now().isoformat()
    _save_sent_history(history)


def _build_html(draft, date_str):
    """Build HTML email from briefing draft JSON."""
    style = (
        "body{font-family:system-ui,-apple-system,sans-serif;max-width:700px;"
        "margin:0 auto;padding:20px;color:#1a1a1a;line-height:1.5}"
        "h1{font-size:22px;border-bottom:2px solid #2563eb;padding-bottom:8px}"
        "h2{font-size:17px;color:#2563eb;margin-top:28px;margin-bottom:8px}"
        "ul{padding-left:18px}li{margin-bottom:10px}"
        "a{color:#2563eb;text-decoration:none}a:hover{text-decoration:underline}"
        ".summary{color:#555;font-size:14px}"
        ".meta{color:#888;font-size:12px;margin-top:2px}"
    )

    parts = [
        f"<html><head><style>{style}</style></head><body>",
        f"<h1>Research Briefing &mdash; {date_str}</h1>",
    ]

    total_articles = 0
    for topic in draft.get("topics", []):
        parts.append(f"<h2>{_esc(topic['name'])}</h2><ul>")
        for article in topic.get("articles", []):
            title = _esc(article.get("title", "Untitled"))
            url = article.get("url", "#")
            summary = _esc(article.get("summary", ""))
            source = _esc(article.get("source", ""))
            date = _esc(article.get("date", ""))
            meta_parts = [p for p in [source, date] if p]
            meta_line = f'<div class="meta">{" &middot; ".join(meta_parts)}</div>' if meta_parts else ""
            parts.append(
                f'<li><a href="{url}">{title}</a>'
                f'<br><span class="summary">{summary}</span>'
                f'{meta_line}</li>'
            )
            total_articles += 1
        parts.append("</ul>")

    parts.append(
        f"<p style='color:#999;font-size:12px;margin-top:30px'>"
        f"{len(draft.get('topics', []))} topics &middot; "
        f"{total_articles} articles</p>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


def _esc(text):
    """Basic HTML escaping."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def main():
    parser = argparse.ArgumentParser(description="Research CLI for chorgi")
    sub = parser.add_subparsers(dest="command", required=True)

    # topics
    topics_parser = sub.add_parser("topics", help="Manage research topics")
    topics_sub = topics_parser.add_subparsers(dest="action", required=True)

    p = topics_sub.add_parser("list", help="List all topics and sources")
    p.set_defaults(func=cmd_topics_list)

    p = topics_sub.add_parser("add", help="Add a topic")
    p.add_argument("name")
    p.add_argument("context", nargs="?", default="")
    p.set_defaults(func=cmd_topics_add)

    p = topics_sub.add_parser("remove", help="Remove a topic")
    p.add_argument("name")
    p.set_defaults(func=cmd_topics_remove)

    # sources
    sources_parser = sub.add_parser("sources", help="Manage sources")
    sources_sub = sources_parser.add_subparsers(dest="action", required=True)

    p = sources_sub.add_parser("list", help="List all sources")
    p.set_defaults(func=cmd_sources_list)

    p = sources_sub.add_parser("add", help="Add a source")
    p.add_argument("name")
    p.set_defaults(func=cmd_sources_add)

    p = sources_sub.add_parser("remove", help="Remove a source")
    p.add_argument("name")
    p.set_defaults(func=cmd_sources_remove)

    # send-briefing
    p = sub.add_parser("send-briefing", help="Format and send the briefing email")
    p.set_defaults(func=cmd_send_briefing)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
