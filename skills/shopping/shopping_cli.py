#!/usr/bin/env python3
"""CLI for managing the shopping list. Data stored in
skills/shopping/workspace/shopping.json (shared with the dashboard API).

Enrichment + storage live in agent/shopping.py so the scraping, price parsing,
and categorization are written once and shared with the web UI.
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "skills"))

import _shared  # noqa: E402
from agent import shopping as sl  # noqa: E402

DATA_FILE = sl.SHOPPING_FILE

_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


def _fmt_price(it) -> str:
    amt = it.get("amount")
    if amt is None:
        return ""
    cur = it.get("currency") or ""
    sym = _SYMBOLS.get(cur)
    return f"{sym}{amt}" if sym else f"{amt} {cur}".strip()


def cmd_add(args):
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else None
    # add_from_url runs the same enrichment (price/image/category) as the web UI.
    item = sl.add_from_url(
        args.url,
        title=args.title or "",
        amount=sl.parse_amount(args.price) if args.price else None,
        currency=args.currency or "",
        notes=args.notes or "",
        tags=tags,
    )
    print(f"Saved: {item.get('title') or args.url}")
    price = _fmt_price(item)
    if price:
        print(f"  Price: {price}")
    if item.get("category"):
        print(f"  Category: {item['category']}")
    if item.get("tags"):
        print(f"  Tags: {', '.join(item['tags'])}")
    unbought = sum(1 for it in sl.load_items() if not it.get("bought"))
    print(f"  ({unbought} to buy)")


def _print_item(it):
    title = it.get("title") or it["url"]
    flag = "✓ " if it.get("bought") else ""
    print(f"  {flag}{title}")
    print(f"  {it['url']}")
    meta = " · ".join(b for b in (_fmt_price(it), it.get("category"), it.get("source")) if b)
    if meta:
        print(f"  {meta}")
    if it.get("tags"):
        print(f"  Tags: {', '.join(it['tags'])}")
    if it.get("notes"):
        print(f"  Notes: {it['notes']}")
    print()


def cmd_list(args):
    items = sl.load_items()
    if args.unbought:
        items = [it for it in items if not it.get("bought")]
    if args.tag:
        tl = args.tag.lower()
        items = [it for it in items
                 if tl in [t.lower() for t in it.get("tags", [])]
                 or tl == (it.get("category") or "").lower()]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("No shopping items found.")
        return
    print(f"{len(items)} item(s):\n")
    for it in items:
        _print_item(it)


def cmd_search(args):
    query = args.query.lower()
    results = []
    for it in sl.load_items():
        hay = " ".join([
            it.get("url", ""), it.get("title", ""), it.get("notes", ""),
            it.get("category", ""), " ".join(it.get("tags", [])),
        ]).lower()
        if query in hay:
            results.append(it)
    if not results:
        print(f"No shopping items matching '{args.query}'.")
        return
    print(f"{len(results)} result(s) for '{args.query}':\n")
    for it in results:
        _print_item(it)


def cmd_set_price(args):
    ok = sl.set_price(args.url, sl.parse_amount(args.price), args.currency or "")
    if not ok:
        print(f"No shopping item found for: {args.url}")
        return
    print(f"Price updated: {args.url}")


def cmd_mark_bought(args):
    ok = sl.set_bought(args.url, not args.undo)
    if not ok:
        print(f"No shopping item found for: {args.url}")
        return
    print(f"Marked {'not bought' if args.undo else 'bought'}: {args.url}")


def cmd_remove(args):
    # load + save under one lock; nothing here re-locks internally.
    with _shared.file_lock(DATA_FILE):
        items = sl.load_items()
        before = len(items)
        items = [it for it in items if it["url"] != args.url]
        if len(items) == before:
            print(f"No shopping item found for: {args.url}")
            return
        sl.save_items(items)
    print(f"Removed: {args.url}")


def main():
    parser = argparse.ArgumentParser(description="Shopping list manager")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a shopping item")
    add_p.add_argument("url", help="Product URL to save")
    add_p.add_argument("--title", "-t", default="", help="Title (auto-fetched if blank)")
    add_p.add_argument("--price", default="", help="Price amount (auto-fetched if blank)")
    add_p.add_argument("--currency", default="", help="Currency code, e.g. EUR (default EUR)")
    add_p.add_argument("--tags", default="", help="Comma-separated tags (auto-categorized if blank)")
    add_p.add_argument("--notes", "-n", default="", help="Notes / context")

    list_p = sub.add_parser("list", help="List shopping items")
    list_p.add_argument("--tag", default="", help="Filter by tag or category")
    list_p.add_argument("--unbought", action="store_true", help="Only not-yet-bought items")
    list_p.add_argument("--limit", type=int, default=0, help="Max results")

    search_p = sub.add_parser("search", help="Search shopping items")
    search_p.add_argument("query", help="Search query")

    price_p = sub.add_parser("set-price", help="Update an item's price")
    price_p.add_argument("url", help="The shopping item's URL")
    price_p.add_argument("price", help="New price amount")
    price_p.add_argument("--currency", default="", help="Currency code, e.g. EUR")

    mark_p = sub.add_parser("mark-bought", help="Mark an item bought")
    mark_p.add_argument("url", help="URL to mark")
    mark_p.add_argument("--undo", action="store_true", help="Mark not-bought instead")

    remove_p = sub.add_parser("remove", help="Remove a shopping item")
    remove_p.add_argument("url", help="URL to remove")

    args = parser.parse_args()
    {
        "add": cmd_add,
        "list": cmd_list,
        "search": cmd_search,
        "set-price": cmd_set_price,
        "mark-bought": cmd_mark_bought,
        "remove": cmd_remove,
    }[args.command](args)


if __name__ == "__main__":
    main()
