#!/usr/bin/env python3
"""CLI wrapper for LinkedIn content operations. Used by the LinkedIn sub-agent via Bash."""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

SKILL_DIR = Path(__file__).parent
WORKSPACE = SKILL_DIR / "workspace"
CALENDAR_FILE = WORKSPACE / "content_calendar.json"
IDEAS_FILE = WORKSPACE / "ideas_backlog.json"
HISTORY_FILE = WORKSPACE / "post_history.json"
DRAFTS_DIR = WORKSPACE / "drafts"

SEEN_POSTS_FILE = WORKSPACE / "seen_posts.json"
FORMATS = ["thought_leadership", "practical_tip", "story", "question", "hot_take", "curated"]


def _load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def _save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


# --- Calendar commands ---

def cmd_calendar_show(args):
    cal = _load_json(CALENDAR_FILE, {"week_of": None, "days": []})
    if not cal["days"]:
        print("No calendar set. Use 'calendar generate' to create context for planning.")
        return
    print(f"Week of: {cal['week_of']}")
    print()
    for day in cal["days"]:
        status = day.get("status", "planned")
        fmt = day.get("format", "?")
        print(f"  {day['date']} ({day.get('weekday', '?')}) [{status}]")
        print(f"    Topic: {day.get('topic', 'TBD')}")
        print(f"    Format: {fmt}")
        if day.get("angle"):
            print(f"    Angle: {day['angle']}")
        print()


def cmd_calendar_generate(args):
    """Output context for the sub-agent to use when generating a weekly calendar."""
    history = _load_json(HISTORY_FILE, {"posts": []})
    ideas = _load_json(IDEAS_FILE, {"ideas": []})
    cal = _load_json(CALENDAR_FILE, {"week_of": None, "days": []})

    # Recent history (last 4 weeks)
    recent = history["posts"][-20:] if history["posts"] else []

    # Format distribution
    fmt_counts = Counter(p.get("format", "unknown") for p in history["posts"])

    context = {
        "current_calendar": cal,
        "recent_posts": recent,
        "format_distribution": dict(fmt_counts),
        "available_formats": FORMATS,
        "unused_ideas": [i for i in ideas["ideas"] if not i.get("used")],
        "total_posts": len(history["posts"]),
    }
    print(json.dumps(context, indent=2))


def cmd_calendar_set(args):
    cal = json.loads(args.json_data)
    if "week_of" not in cal or "days" not in cal:
        print("Error: calendar JSON must have 'week_of' and 'days' keys")
        sys.exit(1)
    # Ensure each day has a status
    for day in cal["days"]:
        day.setdefault("status", "planned")
    _save_json(CALENDAR_FILE, cal)
    print(f"Calendar set for week of {cal['week_of']} with {len(cal['days'])} days")


def cmd_calendar_update(args):
    cal = _load_json(CALENDAR_FILE, {"week_of": None, "days": []})
    found = False
    for day in cal["days"]:
        if day["date"] == args.date:
            day["status"] = args.status
            found = True
            break
    if not found:
        print(f"Date {args.date} not found in calendar")
        sys.exit(1)
    _save_json(CALENDAR_FILE, cal)
    print(f"Updated {args.date} status to '{args.status}'")


# --- Draft commands ---

def cmd_draft(args):
    """Output the calendar entry for a given date or weekday name."""
    cal = _load_json(CALENDAR_FILE, {"week_of": None, "days": []})
    if not cal["days"]:
        print("No calendar set.")
        sys.exit(1)

    query = args.date_or_day.lower()
    for day in cal["days"]:
        if day["date"] == query or day.get("weekday", "").lower() == query:
            print(json.dumps(day, indent=2))
            return

    print(f"No entry found for '{args.date_or_day}'. Available: {', '.join(d['date'] for d in cal['days'])}")
    sys.exit(1)


# --- Ideas commands ---

def cmd_ideas_list(args):
    ideas = _load_json(IDEAS_FILE, {"ideas": []})
    unused = [i for i in ideas["ideas"] if not i.get("used")]
    used = [i for i in ideas["ideas"] if i.get("used")]
    print(f"Unused ideas ({len(unused)}):")
    for i in unused:
        added = i.get("added", "?")
        print(f"  - {i['topic']} (added: {added})")
    if used:
        print(f"\nUsed ideas ({len(used)}):")
        for i in used:
            print(f"  - {i['topic']} (used: {i.get('used_date', '?')})")


def cmd_ideas_add(args):
    ideas = _load_json(IDEAS_FILE, {"ideas": []})
    idea = {
        "topic": args.topic,
        "added": datetime.now().strftime("%Y-%m-%d"),
        "used": False,
    }
    ideas["ideas"].append(idea)
    _save_json(IDEAS_FILE, ideas)
    print(f"Added idea: {args.topic}")


def cmd_ideas_use(args):
    ideas = _load_json(IDEAS_FILE, {"ideas": []})
    topic_lower = args.topic.lower()
    found = False
    for idea in ideas["ideas"]:
        if idea["topic"].lower() == topic_lower:
            idea["used"] = True
            idea["used_date"] = datetime.now().strftime("%Y-%m-%d")
            found = True
            break
    if not found:
        print(f"Idea not found: {args.topic}")
        sys.exit(1)
    _save_json(IDEAS_FILE, ideas)
    print(f"Marked idea as used: {args.topic}")


# --- History commands ---

def cmd_history_log(args):
    history = _load_json(HISTORY_FILE, {"posts": []})
    if args.format not in FORMATS:
        print(f"Invalid format '{args.format}'. Valid: {', '.join(FORMATS)}")
        sys.exit(1)
    entry = {
        "topic": args.topic,
        "format": args.format,
        "date": datetime.now().strftime("%Y-%m-%d"),
    }
    history["posts"].append(entry)
    _save_json(HISTORY_FILE, history)
    print(f"Logged post: {args.topic} ({args.format})")


def cmd_history_show(args):
    history = _load_json(HISTORY_FILE, {"posts": []})
    weeks = args.weeks or 4
    cutoff = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
    recent = [p for p in history["posts"] if p.get("date", "") >= cutoff]
    if not recent:
        print(f"No posts in the last {weeks} weeks.")
        return
    print(f"Posts in last {weeks} weeks ({len(recent)}):")
    for p in recent:
        print(f"  {p['date']}  [{p.get('format', '?')}]  {p['topic']}")


def cmd_history_formats(args):
    history = _load_json(HISTORY_FILE, {"posts": []})
    counts = Counter(p.get("format", "unknown") for p in history["posts"])
    total = len(history["posts"])
    print(f"Format distribution ({total} total posts):")
    for fmt in FORMATS:
        count = counts.get(fmt, 0)
        pct = (count / total * 100) if total else 0
        bar = "\u2588" * int(pct / 5) if total else ""
        print(f"  {fmt:20s}  {count:3d}  ({pct:4.1f}%)  {bar}")
    unknown = counts.get("unknown", 0)
    if unknown:
        print(f"  {'unknown':20s}  {unknown:3d}")


# --- Monitor commands ---

def cmd_monitor_seen(args):
    data = _load_json(SEEN_POSTS_FILE, {"posts": []})
    if not data["posts"]:
        print("No seen posts tracked yet.")
        return
    print(f"Seen posts ({len(data['posts'])}):")
    for p in data["posts"]:
        author = p.get("author", "?")
        title = p.get("title", "?")
        found = p.get("found_at", "?")
        print(f"  [{found}] {author} — {title}")
        print(f"    {p['url']}")


def cmd_monitor_mark(args):
    data = _load_json(SEEN_POSTS_FILE, {"posts": []})
    new_posts = json.loads(args.json_data)
    existing_urls = {p["url"] for p in data["posts"]}
    added = 0
    now = datetime.now().isoformat()
    for post in new_posts:
        if post["url"] not in existing_urls:
            post["found_at"] = now
            data["posts"].append(post)
            existing_urls.add(post["url"])
            added += 1
    # Auto-prune entries older than 7 days
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    data["posts"] = [p for p in data["posts"] if p.get("found_at", "") >= cutoff]
    _save_json(SEEN_POSTS_FILE, data)
    print(f"Marked {added} new post(s) as seen. Total tracked: {len(data['posts'])}")


def cmd_monitor_clear(args):
    _save_json(SEEN_POSTS_FILE, {"posts": []})
    print("Cleared all seen posts.")


def main():
    parser = argparse.ArgumentParser(description="LinkedIn content CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # calendar
    cal_parser = sub.add_parser("calendar", help="Content calendar management")
    cal_sub = cal_parser.add_subparsers(dest="action", required=True)

    p = cal_sub.add_parser("show", help="Show current week's calendar")
    p.set_defaults(func=cmd_calendar_show)

    p = cal_sub.add_parser("generate", help="Output context for calendar generation")
    p.set_defaults(func=cmd_calendar_generate)

    p = cal_sub.add_parser("set", help="Set the weekly calendar from JSON")
    p.add_argument("json_data", help="Full calendar JSON string")
    p.set_defaults(func=cmd_calendar_set)

    p = cal_sub.add_parser("update", help="Update a day's status")
    p.add_argument("date", help="Date (YYYY-MM-DD)")
    p.add_argument("--status", required=True, choices=["planned", "drafted", "posted", "skipped"])
    p.set_defaults(func=cmd_calendar_update)

    # draft
    p = sub.add_parser("draft", help="Get calendar entry for drafting")
    p.add_argument("date_or_day", help="Date (YYYY-MM-DD) or weekday name")
    p.set_defaults(func=cmd_draft)

    # ideas
    ideas_parser = sub.add_parser("ideas", help="Ideas backlog management")
    ideas_sub = ideas_parser.add_subparsers(dest="action", required=True)

    p = ideas_sub.add_parser("list", help="List ideas")
    p.set_defaults(func=cmd_ideas_list)

    p = ideas_sub.add_parser("add", help="Add an idea")
    p.add_argument("topic")
    p.set_defaults(func=cmd_ideas_add)

    p = ideas_sub.add_parser("use", help="Mark idea as used")
    p.add_argument("topic")
    p.set_defaults(func=cmd_ideas_use)

    # history
    hist_parser = sub.add_parser("history", help="Post history")
    hist_sub = hist_parser.add_subparsers(dest="action", required=True)

    p = hist_sub.add_parser("log", help="Log a posted piece")
    p.add_argument("topic")
    p.add_argument("format", choices=FORMATS)
    p.set_defaults(func=cmd_history_log)

    p = hist_sub.add_parser("show", help="Recent post history")
    p.add_argument("--weeks", type=int, default=4)
    p.set_defaults(func=cmd_history_show)

    p = hist_sub.add_parser("formats", help="Format distribution")
    p.set_defaults(func=cmd_history_formats)

    # monitor
    mon_parser = sub.add_parser("monitor", help="FDE post monitoring")
    mon_sub = mon_parser.add_subparsers(dest="action", required=True)

    p = mon_sub.add_parser("seen", help="List seen post URLs")
    p.set_defaults(func=cmd_monitor_seen)

    p = mon_sub.add_parser("mark", help="Mark posts as seen")
    p.add_argument("json_data", help="JSON array of posts: [{url, title, author}]")
    p.set_defaults(func=cmd_monitor_mark)

    p = mon_sub.add_parser("clear", help="Clear all seen posts")
    p.set_defaults(func=cmd_monitor_clear)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
