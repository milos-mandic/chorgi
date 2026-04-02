#!/usr/bin/env python3
"""LinkedIn Growth Management CLI — state management for content planning, drafting, and tracking."""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).parent / "workspace"
CALENDAR_FILE = WORKSPACE / "content_calendar.json"
FEED_FILE = WORKSPACE / "content_feed.json"
HISTORY_FILE = WORKSPACE / "post_history.json"
PILLARS_FILE = WORKSPACE / "pillars.json"
VIRAL_FILE = WORKSPACE / "viral_log.json"
DRAFTS_DIR = WORKSPACE / "drafts"

DEFAULT_PILLARS = {
    "pillars": [
        {
            "id": "the_fde_role",
            "name": "The FDE Role",
            "description": "What the role is, career path, day-to-day, hiring, growth",
            "example_topics": [
                "Career path from SE to FDE",
                "What FDE interviews look like",
                "Day in the life of an FDE Lead",
                "FDE vs Solutions Engineer vs DevRel",
                "Building an FDE team from scratch",
            ],
            "target_frequency": "1x/week",
        },
        {
            "id": "technical_craft",
            "name": "Technical Craft",
            "description": "Demos, POCs, customer engineering, technical excellence",
            "example_topics": [
                "Demo prep checklist",
                "Building POCs that convert",
                "Debugging in customer environments",
                "Technical discovery techniques",
                "Live coding in sales calls",
            ],
            "target_frequency": "1x/week",
        },
        {
            "id": "ai_agents_in_field",
            "name": "AI/Agents in the Field",
            "description": "AI agent deployment, customer-facing AI, enterprise AI adoption",
            "example_topics": [
                "Agent hallucination in demos",
                "Customer trust with AI",
                "Agentic workflows in enterprise",
                "AI coding assistants in field work",
                "Building AI demos that don't break",
            ],
            "target_frequency": "1x/week",
        },
        {
            "id": "fde_hub_community",
            "name": "FDE Hub Community",
            "description": "Newsletter, community building, FDE Hub growth and content",
            "example_topics": [
                "Newsletter recap with unique angle",
                "Community milestone",
                "Reader story or feedback",
                "Behind the scenes of FDE Hub",
            ],
            "target_frequency": "0.5x/week",
        },
        {
            "id": "industry_takes",
            "name": "Industry Takes",
            "description": "Trends affecting FDEs/SEs — market moves, tool shifts, hiring patterns",
            "example_topics": [
                "Big company launches FDE practice",
                "DevRel budget trends",
                "SE compensation data",
                "GTM engineering emergence",
                "Enterprise buying behavior shifts",
            ],
            "target_frequency": "0.5x/week",
        },
    ]
}


def _load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_date(s):
    """Parse date string or weekday name to a date string."""
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    }
    if s.lower() == "today":
        return datetime.now().strftime("%Y-%m-%d")
    if s.lower() == "tomorrow":
        return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    if s.lower() in weekdays:
        target = weekdays[s.lower()]
        today = datetime.now()
        days_ahead = target - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    return s


# ── Calendar ──────────────────────────────────────────────────────────

def calendar_show():
    cal = _load_json(CALENDAR_FILE, None)
    if not cal:
        print("No calendar exists. Generate one first.")
        return
    print(f"Week of {cal['week_of']} (created {cal.get('created_at', 'unknown')})\n")
    for day in cal["days"]:
        status = day["status"].upper()
        pillar = day.get("pillar", "?")
        print(f"  {day['weekday']} {day['date']} [{status}]")
        print(f"    Topic: {day['topic']}")
        print(f"    Format: {day['format']} | Pillar: {pillar}")
        if day.get("angle"):
            print(f"    Angle: {day['angle']}")
        feed_ids = day.get("feed_ids", [])
        if feed_ids:
            print(f"    Feed items: {feed_ids}")
        print()


def calendar_context():
    """Output comprehensive planning context for weekly calendar generation."""
    print("=== PLANNING CONTEXT ===\n")

    # Recent history
    history = _load_json(HISTORY_FILE, {"posts": []})
    recent = history["posts"][-20:]
    if recent:
        print("-- Recent Post History (last 20) --")
        for p in recent:
            print(f"  {p['date']} | {p['format']} | {p.get('pillar', '?')} | {p['topic']}")
        print()
    else:
        print("-- No post history yet --\n")

    # Format distribution
    if recent:
        print("-- Format Distribution --")
        formats = {}
        for p in recent:
            formats[p["format"]] = formats.get(p["format"], 0) + 1
        for fmt, count in sorted(formats.items(), key=lambda x: -x[1]):
            print(f"  {fmt}: {count}")
        print()

    # Pillar rotation
    pillars = _load_json(PILLARS_FILE, DEFAULT_PILLARS)
    print("-- Pillar Rotation (least-recently-used first) --")
    pillar_last_used = {}
    for p in pillars["pillars"]:
        pillar_last_used[p["id"]] = None
    for post in history["posts"]:
        pid = post.get("pillar")
        if pid:
            pillar_last_used[pid] = post["date"]
    sorted_pillars = sorted(pillar_last_used.items(), key=lambda x: x[1] or "0000-00-00")
    for pid, last in sorted_pillars:
        pname = next((p["name"] for p in pillars["pillars"] if p["id"] == pid), pid)
        print(f"  {pname} ({pid}): last used {last or 'never'}")
    print()

    # Unused feed items
    feed = _load_json(FEED_FILE, {"next_id": 1, "items": []})
    unused = [i for i in feed["items"] if not i["used"]]
    if unused:
        print(f"-- Unused Feed Items ({len(unused)}) --")
        for item in unused:
            pillar = item.get("pillar") or "untagged"
            print(f"  [{item['id']}] ({pillar}) {item['content'][:100]}")
            if item.get("key_insight"):
                print(f"       Insight: {item['key_insight']}")
        print()
    else:
        print("-- No unused feed items --\n")

    # Viral patterns
    viral = _load_json(VIRAL_FILE, {"entries": []})
    if viral["entries"]:
        print("-- Viral Post Patterns --")
        for entry in viral["entries"][-5:]:
            print(f"  {entry['date']} | {entry.get('format', '?')} | {entry.get('pillar', '?')}")
            if entry.get("what_worked"):
                print(f"    What worked: {entry['what_worked'][:150]}")
        print()

    # Current calendar (if exists)
    cal = _load_json(CALENDAR_FILE, None)
    if cal:
        print(f"-- Current Calendar (week of {cal['week_of']}) --")
        for day in cal["days"]:
            print(f"  {day['weekday']} {day['date']}: {day['topic']} [{day['status']}]")
        print()


def calendar_set(json_str):
    cal = json.loads(json_str)
    cal["created_at"] = _now_iso()
    _save_json(CALENDAR_FILE, cal)
    print(f"Calendar saved for week of {cal['week_of']} ({len(cal['days'])} days)")


def calendar_update(date, status):
    cal = _load_json(CALENDAR_FILE, None)
    if not cal:
        print("No calendar exists.")
        return
    date = _parse_date(date)
    for day in cal["days"]:
        if day["date"] == date:
            day["status"] = status
            _save_json(CALENDAR_FILE, cal)
            print(f"Updated {date} to {status}")
            return
    print(f"No entry found for {date}")


def calendar_get(date_or_day):
    cal = _load_json(CALENDAR_FILE, None)
    if not cal:
        print("No calendar exists.")
        return
    target = _parse_date(date_or_day)
    for day in cal["days"]:
        if day["date"] == target:
            print(json.dumps(day, indent=2))
            return
    print(f"No entry for {target}")


# ── Content Feed ──────────────────────────────────────────────────────

def feed_list(pillar=None, unused_only=False):
    feed = _load_json(FEED_FILE, {"next_id": 1, "items": []})
    items = feed["items"]
    if unused_only:
        items = [i for i in items if not i["used"]]
    if pillar:
        items = [i for i in items if i.get("pillar") == pillar]
    if not items:
        print("No feed items found.")
        return
    print(f"Feed items ({len(items)}):\n")
    for item in items:
        used = "USED" if item["used"] else "unused"
        p = item.get("pillar") or "untagged"
        print(f"  [{item['id']}] ({p}) [{used}] {item['content'][:120]}")
        if item.get("key_insight"):
            print(f"       Insight: {item['key_insight']}")
        if item.get("url"):
            print(f"       URL: {item['url']}")
        print()


def feed_add(json_str):
    data = json.loads(json_str)
    feed = _load_json(FEED_FILE, {"next_id": 1, "items": []})
    item = {
        "id": feed["next_id"],
        "content": data["content"],
        "source": data.get("source", "manual"),
        "url": data.get("url"),
        "pillar": data.get("pillar"),
        "key_insight": data.get("key_insight"),
        "added": _now_iso(),
        "used": False,
        "used_in": None,
    }
    feed["items"].append(item)
    feed["next_id"] += 1
    _save_json(FEED_FILE, feed)
    print(f"Added feed item [{item['id']}]: {item['content'][:80]}")
    if item["pillar"]:
        print(f"  Pillar: {item['pillar']}")
    if item["key_insight"]:
        print(f"  Insight: {item['key_insight']}")


def feed_use(item_id):
    feed = _load_json(FEED_FILE, {"next_id": 1, "items": []})
    item_id = int(item_id)
    for item in feed["items"]:
        if item["id"] == item_id:
            item["used"] = True
            item["used_in"] = datetime.now().strftime("%Y-%m-%d")
            _save_json(FEED_FILE, feed)
            print(f"Marked feed item [{item_id}] as used")
            return
    print(f"Feed item [{item_id}] not found")


def feed_remove(item_id):
    feed = _load_json(FEED_FILE, {"next_id": 1, "items": []})
    item_id = int(item_id)
    feed["items"] = [i for i in feed["items"] if i["id"] != item_id]
    _save_json(FEED_FILE, feed)
    print(f"Removed feed item [{item_id}]")


# ── Post History ──────────────────────────────────────────────────────

def history_log(json_str):
    data = json.loads(json_str)
    history = _load_json(HISTORY_FILE, {"posts": []})
    post = {
        "date": data["date"],
        "topic": data["topic"],
        "format": data["format"],
        "pillar": data.get("pillar"),
        "draft_file": data.get("draft_file"),
        "performance": data.get("performance"),
    }
    history["posts"].append(post)
    _save_json(HISTORY_FILE, history)
    print(f"Logged: {post['date']} | {post['format']} | {post.get('pillar', '?')} | {post['topic']}")


def history_show(weeks=4):
    history = _load_json(HISTORY_FILE, {"posts": []})
    if not history["posts"]:
        print("No post history yet.")
        return
    cutoff = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
    recent = [p for p in history["posts"] if p["date"] >= cutoff]
    if not recent:
        print(f"No posts in the last {weeks} weeks.")
        return
    print(f"Post history (last {weeks} weeks, {len(recent)} posts):\n")
    for p in recent:
        perf = ""
        if p.get("performance"):
            perf = f" | {p['performance']}"
        print(f"  {p['date']} | {p['format']} | {p.get('pillar', '?')} | {p['topic']}{perf}")


def history_formats():
    history = _load_json(HISTORY_FILE, {"posts": []})
    if not history["posts"]:
        print("No post history yet.")
        return
    formats = {}
    total = len(history["posts"])
    for p in history["posts"]:
        formats[p["format"]] = formats.get(p["format"], 0) + 1
    print(f"Format distribution ({total} total posts):\n")
    for fmt, count in sorted(formats.items(), key=lambda x: -x[1]):
        pct = round(100 * count / total)
        bar = "#" * (count * 2)
        print(f"  {fmt:20s} {count:3d} ({pct:2d}%) {bar}")


def history_pillars():
    history = _load_json(HISTORY_FILE, {"posts": []})
    if not history["posts"]:
        print("No post history yet.")
        return
    pillar_counts = {}
    total = len(history["posts"])
    for p in history["posts"]:
        pid = p.get("pillar", "unknown")
        pillar_counts[pid] = pillar_counts.get(pid, 0) + 1
    print(f"Pillar distribution ({total} total posts):\n")
    for pid, count in sorted(pillar_counts.items(), key=lambda x: -x[1]):
        pct = round(100 * count / total)
        bar = "#" * (count * 2)
        print(f"  {pid:25s} {count:3d} ({pct:2d}%) {bar}")


# ── Pillars ───────────────────────────────────────────────────────────

def pillars_show():
    pillars = _load_json(PILLARS_FILE, DEFAULT_PILLARS)
    if not PILLARS_FILE.exists():
        _save_json(PILLARS_FILE, pillars)
    print("Content Pillars:\n")
    for p in pillars["pillars"]:
        print(f"  {p['name']} ({p['id']})")
        print(f"    {p['description']}")
        print(f"    Target: {p['target_frequency']}")
        print(f"    Examples: {', '.join(p['example_topics'][:3])}")
        print()


def pillars_rotation():
    pillars = _load_json(PILLARS_FILE, DEFAULT_PILLARS)
    history = _load_json(HISTORY_FILE, {"posts": []})
    today = datetime.now().strftime("%Y-%m-%d")

    pillar_last = {}
    pillar_count_4w = {}
    cutoff_4w = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")

    for p in pillars["pillars"]:
        pillar_last[p["id"]] = None
        pillar_count_4w[p["id"]] = 0

    for post in history["posts"]:
        pid = post.get("pillar")
        if pid and pid in pillar_last:
            pillar_last[pid] = post["date"]
            if post["date"] >= cutoff_4w:
                pillar_count_4w[pid] += 1

    print("Pillar Rotation (least-recently-used first):\n")
    sorted_pillars = sorted(pillar_last.items(), key=lambda x: x[1] or "0000-00-00")
    for pid, last in sorted_pillars:
        pname = next((p["name"] for p in pillars["pillars"] if p["id"] == pid), pid)
        freq = next((p["target_frequency"] for p in pillars["pillars"] if p["id"] == pid), "?")
        last_str = last or "never"
        if last:
            days_ago = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
            last_str = f"{last} ({days_ago}d ago)"
        print(f"  {pname:25s} last: {last_str:30s} 4w count: {pillar_count_4w[pid]} (target: {freq})")


# ── Viral Log ─────────────────────────────────────────────────────────

def viral_log(json_str):
    data = json.loads(json_str)
    viral = _load_json(VIRAL_FILE, {"entries": []})
    entry = {
        "date": data["date"],
        "topic": data["topic"],
        "format": data.get("format"),
        "pillar": data.get("pillar"),
        "metrics": data.get("metrics", {}),
        "what_worked": data.get("what_worked"),
        "hook": data.get("hook"),
        "day_of_week": data.get("day_of_week"),
        "logged_at": _now_iso(),
    }
    viral["entries"].append(entry)
    _save_json(VIRAL_FILE, viral)
    print(f"Viral post logged: {entry['date']} | {entry['topic']}")
    if entry["metrics"]:
        m = entry["metrics"]
        print(f"  Metrics: {m.get('likes', '?')} likes, {m.get('comments', '?')} comments, {m.get('impressions', '?')} impressions")


def viral_show(last_n=10):
    viral = _load_json(VIRAL_FILE, {"entries": []})
    if not viral["entries"]:
        print("No viral posts logged yet.")
        return
    entries = viral["entries"][-last_n:]
    print(f"Viral Log (last {len(entries)}):\n")
    for e in entries:
        m = e.get("metrics", {})
        print(f"  {e['date']} | {e.get('format', '?')} | {e.get('pillar', '?')}")
        print(f"    Topic: {e['topic']}")
        print(f"    Metrics: {m.get('likes', '?')}L / {m.get('comments', '?')}C / {m.get('impressions', '?')}I")
        if e.get("what_worked"):
            print(f"    What worked: {e['what_worked'][:150]}")
        if e.get("hook"):
            print(f"    Hook: {e['hook'][:100]}")
        print()


def viral_patterns():
    viral = _load_json(VIRAL_FILE, {"entries": []})
    if not viral["entries"]:
        print("No viral posts to analyze yet.")
        return

    entries = viral["entries"]
    print(f"Viral Pattern Analysis ({len(entries)} posts):\n")

    # Format distribution
    formats = {}
    for e in entries:
        f = e.get("format", "unknown")
        formats[f] = formats.get(f, 0) + 1
    print("  By format:")
    for f, c in sorted(formats.items(), key=lambda x: -x[1]):
        print(f"    {f}: {c}")

    # Pillar distribution
    pillars = {}
    for e in entries:
        p = e.get("pillar", "unknown")
        pillars[p] = pillars.get(p, 0) + 1
    print("\n  By pillar:")
    for p, c in sorted(pillars.items(), key=lambda x: -x[1]):
        print(f"    {p}: {c}")

    # Day of week
    days = {}
    for e in entries:
        d = e.get("day_of_week", "unknown")
        days[d] = days.get(d, 0) + 1
    print("\n  By day:")
    for d, c in sorted(days.items(), key=lambda x: -x[1]):
        print(f"    {d}: {c}")

    # Average metrics
    likes = [e["metrics"].get("likes", 0) for e in entries if e.get("metrics")]
    comments = [e["metrics"].get("comments", 0) for e in entries if e.get("metrics")]
    if likes:
        print(f"\n  Avg metrics: {sum(likes)//len(likes)} likes, {sum(comments)//len(comments)} comments")

    # Hooks
    hooks = [e["hook"] for e in entries if e.get("hook")]
    if hooks:
        print(f"\n  Hooks from viral posts:")
        for h in hooks[-5:]:
            print(f"    \"{h[:100]}\"")


# ── CLI Router ────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: python3 linkedin_cli.py <group> <command> [args]")
        print("Groups: calendar, feed, history, pillars, viral")
        sys.exit(1)

    group, cmd = args[0], args[1]
    rest = args[2:]

    if group == "calendar":
        if cmd == "show":
            calendar_show()
        elif cmd == "context":
            calendar_context()
        elif cmd == "set":
            calendar_set(rest[0])
        elif cmd == "update":
            date = rest[0]
            status = rest[rest.index("--status") + 1] if "--status" in rest else rest[1]
            calendar_update(date, status)
        elif cmd == "get":
            calendar_get(rest[0])
        else:
            print(f"Unknown calendar command: {cmd}")

    elif group == "feed":
        if cmd == "list":
            pillar = None
            unused = False
            if "--pillar" in rest:
                pillar = rest[rest.index("--pillar") + 1]
            if "--unused" in rest:
                unused = True
            feed_list(pillar=pillar, unused_only=unused)
        elif cmd == "add":
            feed_add(rest[0])
        elif cmd == "use":
            feed_use(rest[0])
        elif cmd == "remove":
            feed_remove(rest[0])
        else:
            print(f"Unknown feed command: {cmd}")

    elif group == "history":
        if cmd == "log":
            history_log(rest[0])
        elif cmd == "show":
            weeks = 4
            if "--weeks" in rest:
                weeks = int(rest[rest.index("--weeks") + 1])
            history_show(weeks)
        elif cmd == "formats":
            history_formats()
        elif cmd == "pillars":
            history_pillars()
        else:
            print(f"Unknown history command: {cmd}")

    elif group == "pillars":
        if cmd == "show":
            pillars_show()
        elif cmd == "rotation":
            pillars_rotation()
        else:
            print(f"Unknown pillars command: {cmd}")

    elif group == "viral":
        if cmd == "log":
            viral_log(rest[0])
        elif cmd == "show":
            last_n = 10
            if "--last" in rest:
                last_n = int(rest[rest.index("--last") + 1])
            viral_show(last_n)
        elif cmd == "patterns":
            viral_patterns()
        else:
            print(f"Unknown viral command: {cmd}")

    else:
        print(f"Unknown group: {group}")
        print("Groups: calendar, feed, history, pillars, viral")
        sys.exit(1)


if __name__ == "__main__":
    main()
