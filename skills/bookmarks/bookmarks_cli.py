#!/usr/bin/env python3
"""CLI for managing bookmarks. Data stored in workspace/bookmarks.json."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_FILE = Path(__file__).parent / "workspace" / "bookmarks.json"


def load_bookmarks() -> list[dict]:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return []


def save_bookmarks(bookmarks: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(bookmarks, indent=2))


def cmd_add(args):
    bookmarks = load_bookmarks()

    # Check for duplicate URL
    for b in bookmarks:
        if b["url"] == args.url:
            print(f"Already bookmarked: {b['title'] or b['url']}")
            return

    bookmark = {
        "url": args.url,
        "title": args.title or "",
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else [],
        "notes": args.notes or "",
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    bookmarks.insert(0, bookmark)
    save_bookmarks(bookmarks)
    print(f"Saved: {bookmark['title'] or bookmark['url']}")
    if bookmark["tags"]:
        print(f"Tags: {', '.join(bookmark['tags'])}")


def cmd_list(args):
    bookmarks = load_bookmarks()

    if args.tag:
        tag_lower = args.tag.lower()
        bookmarks = [b for b in bookmarks if tag_lower in [t.lower() for t in b.get("tags", [])]]

    if args.limit:
        bookmarks = bookmarks[: args.limit]

    if not bookmarks:
        print("No bookmarks found.")
        return

    print(f"{len(bookmarks)} bookmark(s):\n")
    for b in bookmarks:
        title = b.get("title") or b["url"]
        date = b.get("saved_at", "")[:10]
        tags = ", ".join(b.get("tags", []))
        print(f"  {title}")
        print(f"  {b['url']}")
        if tags:
            print(f"  Tags: {tags}")
        if b.get("notes"):
            print(f"  Notes: {b['notes']}")
        print(f"  Saved: {date}")
        print()


def cmd_search(args):
    bookmarks = load_bookmarks()
    query = args.query.lower()

    results = []
    for b in bookmarks:
        searchable = " ".join([
            b.get("url", ""),
            b.get("title", ""),
            b.get("notes", ""),
            " ".join(b.get("tags", [])),
        ]).lower()
        if query in searchable:
            results.append(b)

    if not results:
        print(f"No bookmarks matching '{args.query}'.")
        return

    print(f"{len(results)} result(s) for '{args.query}':\n")
    for b in results:
        title = b.get("title") or b["url"]
        tags = ", ".join(b.get("tags", []))
        print(f"  {title}")
        print(f"  {b['url']}")
        if tags:
            print(f"  Tags: {tags}")
        if b.get("notes"):
            print(f"  Notes: {b['notes']}")
        print()


def cmd_remove(args):
    bookmarks = load_bookmarks()
    original_count = len(bookmarks)
    bookmarks = [b for b in bookmarks if b["url"] != args.url]

    if len(bookmarks) == original_count:
        print(f"No bookmark found for: {args.url}")
        return

    save_bookmarks(bookmarks)
    print(f"Removed: {args.url}")


def main():
    parser = argparse.ArgumentParser(description="Bookmark manager")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a bookmark")
    add_p.add_argument("url", help="URL to bookmark")
    add_p.add_argument("--title", "-t", default="", help="Title")
    add_p.add_argument("--tags", default="", help="Comma-separated tags")
    add_p.add_argument("--notes", "-n", default="", help="Notes")

    list_p = sub.add_parser("list", help="List bookmarks")
    list_p.add_argument("--tag", default="", help="Filter by tag")
    list_p.add_argument("--limit", type=int, default=0, help="Max results")

    search_p = sub.add_parser("search", help="Search bookmarks")
    search_p.add_argument("query", help="Search query")

    remove_p = sub.add_parser("remove", help="Remove a bookmark")
    remove_p.add_argument("url", help="URL to remove")

    args = parser.parse_args()
    {"add": cmd_add, "list": cmd_list, "search": cmd_search, "remove": cmd_remove}[args.command](args)


if __name__ == "__main__":
    main()
