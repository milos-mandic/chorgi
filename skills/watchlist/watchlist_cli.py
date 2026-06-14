#!/usr/bin/env python3
"""CLI for managing the watch list. Data stored in
skills/watchlist/workspace/watchlist.json (shared with the agent's Haiku
fast-path and the web UI).

Enrichment + storage live in agent/watchlist.py so the fetch logic (og tags,
oEmbed, rating/duration) is written once.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "skills"))

import _shared  # noqa: E402
from agent import watchlist as wl  # noqa: E402

DATA_FILE = wl.WATCHLIST_FILE


def cmd_add(args):
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    meta = {}
    if not args.no_fetch:
        meta = wl.fetch_watch_meta(args.url)
    title = args.title or meta.get("title") or ""
    summary = meta.get("description") or ""
    # add_watch_item takes its own cross-process lock internally.
    unwatched = wl.add_watch_item(
        args.url,
        title=title,
        summary=summary,
        image=meta.get("image", ""),
        rating=meta.get("rating", ""),
        duration=meta.get("duration", ""),
        source=wl.source_of(args.url),
        notes=args.notes or "",
        tags=tags,
        where_url=args.where or "",
    )
    print(f"Saved: {title or args.url}")
    if args.where:
        print(f"  Where to watch: {args.where}")
    bits = [b for b in (meta.get("rating") and f"★ {meta['rating']}",
                        meta.get("duration")) if b]
    if bits:
        print("  " + " · ".join(bits))
    if tags:
        print(f"  Tags: {', '.join(tags)}")
    print(f"  ({unwatched} unwatched)")


def _print_item(it):
    title = it.get("title") or it["url"]
    flag = "✓ " if it.get("watched") else ""
    print(f"  {flag}{title}")
    print(f"  {it['url']}")
    meta = " · ".join(b for b in (
        it.get("source"),
        it.get("rating") and f"★ {it['rating']}",
        it.get("duration"),
    ) if b)
    if meta:
        print(f"  {meta}")
    if it.get("where_url"):
        label = it.get("where_source") or "link"
        print(f"  Watch on {label}: {it['where_url']}")
    if it.get("tags"):
        print(f"  Tags: {', '.join(it['tags'])}")
    if it.get("notes"):
        print(f"  Notes: {it['notes']}")
    if it.get("summary"):
        print(f"  Summary: {it['summary'][:140]}")
    print()


def cmd_list(args):
    items = wl.load_watch_items()
    if args.unwatched:
        items = [it for it in items if not it.get("watched")]
    if args.tag:
        tag_lower = args.tag.lower()
        items = [it for it in items if tag_lower in [t.lower() for t in it.get("tags", [])]]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("No watch items found.")
        return
    print(f"{len(items)} item(s):\n")
    for it in items:
        _print_item(it)


def cmd_search(args):
    query = args.query.lower()
    results = []
    for it in wl.load_watch_items():
        hay = " ".join([
            it.get("url", ""), it.get("title", ""), it.get("notes", ""),
            it.get("summary", ""), " ".join(it.get("tags", [])),
        ]).lower()
        if query in hay:
            results.append(it)
    if not results:
        print(f"No watch items matching '{args.query}'.")
        return
    print(f"{len(results)} result(s) for '{args.query}':\n")
    for it in results:
        _print_item(it)


def cmd_mark_watched(args):
    ok = wl.set_watched(args.url, not args.undo)
    if not ok:
        print(f"No watch item found for: {args.url}")
        return
    print(f"Marked {'unwatched' if args.undo else 'watched'}: {args.url}")


def cmd_set_where(args):
    ok = wl.set_where(args.url, args.where_url)
    if not ok:
        print(f"No watch item found for: {args.url}")
        return
    if args.where_url:
        print(f"Where to watch set ({wl.source_of(args.where_url)}): {args.url}")
    else:
        print(f"Cleared where-to-watch link: {args.url}")


def cmd_remove(args):
    # load + save under one lock; nothing here re-locks internally.
    with _shared.file_lock(DATA_FILE):
        items = wl.load_watch_items()
        before = len(items)
        items = [it for it in items if it["url"] != args.url]
        if len(items) == before:
            print(f"No watch item found for: {args.url}")
            return
        wl.save_watch_items(items)
    print(f"Removed: {args.url}")


def main():
    parser = argparse.ArgumentParser(description="Watch list manager")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a watch item")
    add_p.add_argument("url", help="URL to save")
    add_p.add_argument("--title", "-t", default="", help="Title (auto-fetched if blank)")
    add_p.add_argument("--tags", default="", help="Comma-separated tags")
    add_p.add_argument("--notes", "-n", default="", help="Notes")
    add_p.add_argument("--where", default="", help="A 'where to watch' link (e.g. Netflix)")
    add_p.add_argument("--no-fetch", action="store_true", help="Skip metadata fetch")

    list_p = sub.add_parser("list", help="List watch items")
    list_p.add_argument("--tag", default="", help="Filter by tag")
    list_p.add_argument("--unwatched", action="store_true", help="Only unwatched items")
    list_p.add_argument("--limit", type=int, default=0, help="Max results")

    search_p = sub.add_parser("search", help="Search watch items")
    search_p.add_argument("query", help="Search query")

    mark_p = sub.add_parser("mark-watched", help="Mark an item watched")
    mark_p.add_argument("url", help="URL to mark")
    mark_p.add_argument("--undo", action="store_true", help="Mark unwatched instead")

    where_p = sub.add_parser("set-where", help="Attach a 'where to watch' link to an item")
    where_p.add_argument("url", help="The watch item's URL (e.g. the IMDB link)")
    where_p.add_argument("where_url", help="Where to watch it (e.g. a Netflix link); '' clears it")

    remove_p = sub.add_parser("remove", help="Remove a watch item")
    remove_p.add_argument("url", help="URL to remove")

    args = parser.parse_args()
    {
        "add": cmd_add,
        "list": cmd_list,
        "search": cmd_search,
        "mark-watched": cmd_mark_watched,
        "set-where": cmd_set_where,
        "remove": cmd_remove,
    }[args.command](args)


if __name__ == "__main__":
    main()
