#!/usr/bin/env python3
"""CLI for managing tasks. Data stored in workspace/tasks.json."""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_FILE = Path(__file__).parent / "workspace" / "tasks.json"


def load_tasks() -> list[dict]:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return []


def save_tasks(tasks: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(tasks, indent=2))


def make_id() -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    suffix = hashlib.md5(str(now).encode() + str(len(load_tasks())).encode()).hexdigest()[:3]
    return f"t_{now}_{suffix}"


def find_task(tasks: list[dict], task_id: str) -> dict | None:
    for t in tasks:
        if t["id"] == task_id:
            return t
    # Allow prefix match
    matches = [t for t in tasks if t["id"].startswith(task_id)]
    if len(matches) == 1:
        return matches[0]
    return None


def cmd_add(args):
    tasks = load_tasks()
    task = {
        "id": make_id(),
        "title": args.title,
        "notes": args.notes or "",
        "priority": args.priority or "medium",
        "estimated_minutes": args.estimate,
        "deadline": args.deadline,
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else [],
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "carry_count": 0,
    }
    tasks.insert(0, task)
    save_tasks(tasks)
    print(f"Added: {task['title']} [{task['id']}]")
    if task["priority"] != "medium":
        print(f"Priority: {task['priority']}")
    if task["deadline"]:
        print(f"Deadline: {task['deadline']}")
    if task["tags"]:
        print(f"Tags: {', '.join(task['tags'])}")


def cmd_list(args):
    tasks = load_tasks()

    status_filter = args.status or "pending"
    if status_filter != "all":
        tasks = [t for t in tasks if t["status"] == status_filter]

    if args.tag:
        tag_lower = args.tag.lower()
        tasks = [t for t in tasks if tag_lower in [tg.lower() for tg in t.get("tags", [])]]

    if not tasks:
        label = "tasks" if status_filter == "all" else f"{status_filter} tasks"
        print(f"No {label} found.")
        return

    # Sort: high priority first, then by deadline (soonest first), then by creation
    priority_order = {"high": 0, "medium": 1, "low": 2}
    tasks.sort(key=lambda t: (
        priority_order.get(t.get("priority", "medium"), 1),
        t.get("deadline") or "9999-99-99",
    ))

    print(f"{len(tasks)} task(s):\n")
    for t in tasks:
        priority_mark = {"high": "!!!", "medium": "", "low": "(low)"}
        mark = priority_mark.get(t.get("priority", "medium"), "")
        status = t["status"]
        prefix = "[x]" if status == "done" else "[ ]" if status == "pending" else f"[{status}]"

        line = f"  {prefix} {t['title']}"
        if mark:
            line += f"  {mark}"
        print(line)
        print(f"      id: {t['id']}")
        if t.get("deadline"):
            print(f"      deadline: {t['deadline']}")
        if t.get("estimated_minutes"):
            print(f"      estimate: {t['estimated_minutes']}min")
        if t.get("tags"):
            print(f"      tags: {', '.join(t['tags'])}")
        if t.get("notes"):
            print(f"      notes: {t['notes']}")
        if t.get("carry_count", 0) > 0:
            print(f"      carried: {t['carry_count']}x")
        print()


def cmd_done(args):
    tasks = load_tasks()
    task = find_task(tasks, args.task_id)
    if not task:
        print(f"Task not found: {args.task_id}")
        sys.exit(1)
    task["status"] = "done"
    task["completed_at"] = datetime.now(timezone.utc).isoformat()
    save_tasks(tasks)
    print(f"Done: {task['title']}")


def cmd_remove(args):
    tasks = load_tasks()
    task = find_task(tasks, args.task_id)
    if not task:
        print(f"Task not found: {args.task_id}")
        sys.exit(1)
    title = task["title"]
    tasks.remove(task)
    save_tasks(tasks)
    print(f"Removed: {title}")


def cmd_update(args):
    tasks = load_tasks()
    task = find_task(tasks, args.task_id)
    if not task:
        print(f"Task not found: {args.task_id}")
        sys.exit(1)

    if args.title is not None:
        task["title"] = args.title
    if args.priority is not None:
        task["priority"] = args.priority
    if args.estimate is not None:
        task["estimated_minutes"] = args.estimate
    if args.deadline is not None:
        task["deadline"] = args.deadline
    if args.notes is not None:
        task["notes"] = args.notes
    if args.tags is not None:
        task["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.status is not None:
        task["status"] = args.status
    if args.carry_count is not None:
        task["carry_count"] = args.carry_count

    save_tasks(tasks)
    print(f"Updated: {task['title']} [{task['id']}]")


def cmd_pending_json(args):
    tasks = load_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]
    print(json.dumps(pending, indent=2))


def cmd_clear_done(args):
    tasks = load_tasks()
    before = len(tasks)
    tasks = [t for t in tasks if t["status"] != "done"]
    removed = before - len(tasks)
    save_tasks(tasks)
    print(f"Cleared {removed} completed task(s).")


def _setup_calendar_imports():
    """Add calendar skill to sys.path and load secrets.env if needed."""
    cal_dir = Path(__file__).resolve().parent.parent / "calendar"
    if str(cal_dir) not in sys.path:
        sys.path.insert(0, str(cal_dir))

    secrets = Path(__file__).resolve().parent.parent.parent / ".personal" / "secrets.env"
    if not os.environ.get("CALENDAR_OWNER_ID") and secrets.exists():
        for line in secrets.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def cmd_schedule_batch(args):
    """Batch-schedule pending tasks into free calendar slots."""
    import os as _os
    from zoneinfo import ZoneInfo

    _setup_calendar_imports()
    import calendar_client
    import scheduler as cal_scheduler

    prefs = cal_scheduler.load_preferences()
    CET = ZoneInfo("Europe/Berlin")

    # 1. Load pending tasks
    tasks = load_tasks()
    pending = [t for t in tasks if t["status"] == "pending"]
    if not pending:
        print(json.dumps({"scheduled": [], "deferred": [], "warnings": {},
                           "summary": "No pending tasks to schedule."}))
        return

    # 2. Sort by priority (high first), then deadline (soonest), then carry_count (most deferred)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    pending.sort(key=lambda t: (
        priority_order.get(t.get("priority", "medium"), 1),
        t.get("deadline") or "9999-99-99",
        -t.get("carry_count", 0),
    ))

    # 3. Get free slots
    owner_id, bot_id = calendar_client._get_calendar_ids()
    now = datetime.now(timezone.utc)
    end_time = now + timedelta(days=args.days)
    default_duration = prefs.get("default_duration", 60)
    buffer_min = prefs.get("buffer_minutes", 15)

    raw_slots = calendar_client.find_free_slots(
        owner_id, bot_id, now, end_time, duration_minutes=30
    )

    # 4. Filter slots to allowed scheduling windows (weekday evenings, full weekends)
    def _filter_to_allowed(slot_start, slot_end):
        """Return list of (start, end) sub-ranges within allowed hours."""
        allowed = []
        cursor = slot_start
        while cursor < slot_end:
            local = cursor.astimezone(CET)
            day_end = slot_end

            if local.weekday() < 5:  # Weekday — only 17:30-22:00 CET
                evening_start = local.replace(hour=17, minute=30, second=0, microsecond=0)
                evening_end = local.replace(hour=22, minute=0, second=0, microsecond=0)
                if local < evening_start:
                    cursor = evening_start.astimezone(timezone.utc)
                    continue
                if local >= evening_end:
                    # Jump to next day 00:00 CET
                    next_day = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    cursor = next_day.astimezone(timezone.utc)
                    continue
                block_end = min(day_end, evening_end.astimezone(timezone.utc))
                allowed.append((cursor, block_end))
                cursor = block_end
            else:  # Weekend — fully available
                # End at midnight next day CET
                next_midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                block_end = min(day_end, next_midnight.astimezone(timezone.utc))
                allowed.append((cursor, block_end))
                cursor = block_end

        return allowed

    available_slots = []
    for slot in raw_slots:
        try:
            s = datetime.fromisoformat(slot["start"])
            e = datetime.fromisoformat(slot["end"])
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
        for sub_start, sub_end in _filter_to_allowed(s, e):
            if (sub_end - sub_start).total_seconds() >= 30 * 60:
                available_slots.append({"start": sub_start, "end": sub_end})

    # 5. Group tasks by overlapping tags
    def _group_by_tags(task_list):
        """Group tasks sharing any tag. Returns list of lists."""
        groups = []
        assigned = set()
        for i, t in enumerate(task_list):
            if i in assigned:
                continue
            group = [t]
            assigned.add(i)
            tags_i = set(tag.lower() for tag in t.get("tags", []))
            if tags_i:
                for j, t2 in enumerate(task_list):
                    if j in assigned:
                        continue
                    tags_j = set(tag.lower() for tag in t2.get("tags", []))
                    if tags_i & tags_j:
                        group.append(t2)
                        assigned.add(j)
                        tags_i |= tags_j
            groups.append(group)
        return groups

    task_groups = _group_by_tags(pending)

    # 6. Greedy assignment
    scheduled = []
    deferred = []
    buffer = timedelta(minutes=buffer_min)

    for group in task_groups:
        for task in group:
            duration = timedelta(minutes=task.get("estimated_minutes") or default_duration)
            placed = False

            for slot in available_slots:
                slot_dur = slot["end"] - slot["start"]
                if slot_dur >= duration:
                    task_start = slot["start"]
                    task_end = task_start + duration

                    # Create calendar event
                    event_id = None
                    if not args.dry_run:
                        try:
                            result = calendar_client.create_event(
                                bot_id,
                                f"Task: {task['title']}",
                                task_start,
                                task_end,
                                description=f"Auto-scheduled from task list. ID: {task['id']}",
                                attendees=[owner_id],
                            )
                            event_id = result.get("id")
                        except Exception as exc:
                            deferred.append({
                                "task_id": task["id"], "title": task["title"],
                                "reason": f"Calendar error: {exc}",
                            })
                            break

                    scheduled.append({
                        "task_id": task["id"],
                        "title": task["title"],
                        "start": task_start.astimezone(CET).strftime("%Y-%m-%d %H:%M"),
                        "end": task_end.astimezone(CET).strftime("%H:%M"),
                        "event_id": event_id,
                    })

                    # Shrink slot (consume used time + buffer)
                    new_start = task_end + buffer
                    if new_start < slot["end"]:
                        slot["start"] = new_start
                    else:
                        available_slots.remove(slot)

                    placed = True
                    break

            if not placed:
                task["carry_count"] = task.get("carry_count", 0) + 1
                info = {"task_id": task["id"], "title": task["title"],
                        "carry_count": task["carry_count"]}
                if task.get("deadline"):
                    info["deadline"] = task["deadline"]
                deferred.append(info)

    # 7. Update task statuses
    if not args.dry_run:
        all_tasks = load_tasks()
        scheduled_ids = {s["task_id"] for s in scheduled}
        deferred_map = {d["task_id"]: d.get("carry_count") for d in deferred}
        for t in all_tasks:
            if t["id"] in scheduled_ids:
                t["status"] = "scheduled"
            elif t["id"] in deferred_map and deferred_map[t["id"]] is not None:
                t["carry_count"] = deferred_map[t["id"]]
        save_tasks(all_tasks)

    # 8. Build warnings
    warnings = {}
    deadline_urgent = []
    chronically_deferred = []
    for d in deferred:
        if d.get("deadline"):
            try:
                dl = datetime.strptime(d["deadline"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_left = (dl - now).days
                if days_left <= 3:
                    deadline_urgent.append({
                        "task_id": d["task_id"], "title": d["title"],
                        "deadline": d["deadline"], "days_remaining": days_left,
                    })
            except ValueError:
                pass
        if d.get("carry_count", 0) >= 3:
            chronically_deferred.append({
                "task_id": d["task_id"], "title": d["title"],
                "carry_count": d["carry_count"],
            })
    if deadline_urgent:
        warnings["deadline_urgent"] = deadline_urgent
    if chronically_deferred:
        warnings["chronically_deferred"] = chronically_deferred

    # 9. Summary
    parts = [f"Scheduled {len(scheduled)} task(s)"]
    if deferred:
        parts.append(f"deferred {len(deferred)}")
    if deadline_urgent:
        parts.append(f"{len(deadline_urgent)} deadline warning(s)")
    if chronically_deferred:
        parts.append(f"{len(chronically_deferred)} chronically deferred")
    summary = ", ".join(parts) + "."

    if args.dry_run:
        summary = "[DRY RUN] " + summary

    output = {
        "scheduled": scheduled,
        "deferred": deferred,
        "warnings": warnings,
        "summary": summary,
    }
    print(json.dumps(output, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Task manager")
    sub = parser.add_subparsers(dest="command", required=True)

    add_p = sub.add_parser("add", help="Add a task")
    add_p.add_argument("title", help="Task title")
    add_p.add_argument("--notes", "-n", default="", help="Notes")
    add_p.add_argument("--priority", "-p", choices=["high", "medium", "low"], default=None, help="Priority")
    add_p.add_argument("--estimate", "-e", type=int, default=None, help="Estimated minutes")
    add_p.add_argument("--deadline", "-d", default=None, help="Deadline (YYYY-MM-DD)")
    add_p.add_argument("--tags", "-t", default="", help="Comma-separated tags")

    list_p = sub.add_parser("list", help="List tasks")
    list_p.add_argument("--status", "-s", choices=["pending", "scheduled", "done", "all"], default=None, help="Filter by status")
    list_p.add_argument("--tag", default="", help="Filter by tag")

    done_p = sub.add_parser("done", help="Mark task as done")
    done_p.add_argument("task_id", help="Task ID")

    remove_p = sub.add_parser("remove", help="Remove a task")
    remove_p.add_argument("task_id", help="Task ID")

    update_p = sub.add_parser("update", help="Update a task")
    update_p.add_argument("task_id", help="Task ID")
    update_p.add_argument("--title", default=None)
    update_p.add_argument("--priority", "-p", choices=["high", "medium", "low"], default=None)
    update_p.add_argument("--estimate", "-e", type=int, default=None)
    update_p.add_argument("--deadline", "-d", default=None)
    update_p.add_argument("--notes", "-n", default=None)
    update_p.add_argument("--tags", "-t", default=None)
    update_p.add_argument("--status", default=None, choices=["pending", "scheduled", "done"])
    update_p.add_argument("--carry-count", type=int, default=None)

    sub.add_parser("pending-json", help="Dump pending tasks as JSON")
    sub.add_parser("clear-done", help="Remove all completed tasks")

    batch_p = sub.add_parser("schedule-batch", help="Batch-schedule pending tasks into calendar")
    batch_p.add_argument("--days", type=int, default=2, help="Days ahead to search (default 2)")
    batch_p.add_argument("--dry-run", action="store_true", help="Show plan without creating events")

    args = parser.parse_args()
    cmds = {
        "add": cmd_add,
        "list": cmd_list,
        "done": cmd_done,
        "remove": cmd_remove,
        "update": cmd_update,
        "pending-json": cmd_pending_json,
        "clear-done": cmd_clear_done,
        "schedule-batch": cmd_schedule_batch,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
