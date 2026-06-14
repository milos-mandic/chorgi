#!/usr/bin/env python3
"""CLI for managing tasks. Data stored in workspace/tasks.json."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Deliberate cross-skill dependency: scheduling commands shell out to the
# calendar skill's CLI and parse its JSON stdout. If calendar_cli.py moves
# or its output contract changes, schedule-batch and --scheduled-at break.
CALENDAR_CLI = Path(__file__).resolve().parent.parent / "calendar" / "calendar_cli.py"
# Calendar CLI needs google-api-python-client etc. — only the project venv has them.
# Falls back to sys.executable if the venv binary is missing.
_VENV_PY = Path(__file__).resolve().parent.parent.parent / ".venv" / "bin" / "python3"
PYTHON_FOR_CALENDAR = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

DATA_FILE = Path(__file__).parent / "workspace" / "tasks.json"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _shared  # noqa: E402


def load_tasks() -> list[dict]:
    return _shared.load_json(DATA_FILE, [])


def save_tasks(tasks: list[dict]) -> None:
    _shared.save_json(DATA_FILE, tasks)


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
        "time_class": getattr(args, "time_class", None) or "anytime",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "carry_count": 0,
    }

    scheduled_at = getattr(args, "scheduled_at", None)
    if scheduled_at:
        result = create_calendar_event_for_task(task, scheduled_at)
        if result.get("ok"):
            task["status"] = "scheduled"
            task["scheduled_at"] = result["scheduled_at"]
            task["calendar_event_id"] = result["event_id"]
        else:
            # Keep the task pending but remember the time the user asked for, so
            # it isn't silently lost and the batch planner can prefer it later.
            task["requested_at"] = scheduled_at
            print(f"Warning: calendar event not created ({result.get('error', 'unknown')}). Task saved as pending.")

    tasks.insert(0, task)
    save_tasks(tasks)
    print(f"Added: {task['title']} [{task['id']}]")
    if task["priority"] != "medium":
        print(f"Priority: {task['priority']}")
    if task["status"] == "scheduled":
        print(f"Scheduled: {task['scheduled_at']}")
    if task["deadline"]:
        print(f"Deadline: {task['deadline']}")
    if task["tags"]:
        print(f"Tags: {', '.join(task['tags'])}")


def _parse_scheduled_at(value: str) -> datetime:
    """Parse a 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM' string as local time."""
    s = value.strip().replace("T", " ")
    # tolerate trailing seconds
    fmt = "%Y-%m-%d %H:%M:%S" if s.count(":") == 2 else "%Y-%m-%d %H:%M"
    dt = datetime.strptime(s, fmt)
    return dt.replace(tzinfo=_shared.LOCAL_TZ)


def _run_calendar_cli(args: list[str]) -> dict:
    """Invoke calendar_cli.py as a subprocess; return parsed-JSON stdout or {ok: False, error}."""
    try:
        proc = subprocess.run(
            [PYTHON_FOR_CALENDAR, str(CALENDAR_CLI), *args],
            capture_output=True, text=True, timeout=60,
        )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"calendar_cli output not JSON: {proc.stdout[:200] or proc.stderr[:200]}"}
        if proc.returncode != 0 or "error" in payload:
            return {"ok": False, "error": payload.get("error") or f"calendar_cli exited {proc.returncode}"}
        return {"ok": True, "data": payload}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "calendar_cli timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create_calendar_event_for_task(task: dict, scheduled_at: str) -> dict:
    """Create a bot-calendar event for the given task via calendar_cli subprocess."""
    start = _parse_scheduled_at(scheduled_at)
    duration = int(task.get("estimated_minutes") or 60)
    end = start + timedelta(minutes=duration)
    args = [
        "create", f"Task: {task['title']}",
        start.strftime("%Y-%m-%d %H:%M"),
        end.strftime("%Y-%m-%d %H:%M"),
        "--force",
    ]
    # Provenance in the description; the canonical task<->event link is the
    # stored calendar_event_id, not the title or description.
    description = task.get("notes") or ""
    description = f"{description}\nID: {task['id']}".strip()
    args.extend(["--description", description])
    res = _run_calendar_cli(args)
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}
    event = res["data"]
    return {"ok": True, "event_id": event.get("id", ""), "scheduled_at": start.isoformat()}


def update_calendar_event_for_task(task: dict, scheduled_at: str) -> dict:
    """Update the event time on the bot calendar via calendar_cli subprocess."""
    start = _parse_scheduled_at(scheduled_at)
    duration = int(task.get("estimated_minutes") or 60)
    end = start + timedelta(minutes=duration)
    args = [
        "update", task["calendar_event_id"],
        "--title", f"Task: {task['title']}",
        "--start", start.strftime("%Y-%m-%d %H:%M"),
        "--end", end.strftime("%Y-%m-%d %H:%M"),
    ]
    if task.get("notes"):
        args.extend(["--description", task["notes"]])
    res = _run_calendar_cli(args)
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}
    return {"ok": True, "scheduled_at": start.isoformat()}


def delete_calendar_event_for_task(task: dict) -> dict:
    """Delete the bot-calendar event tied to this task via calendar_cli subprocess."""
    res = _run_calendar_cli(["delete", task["calendar_event_id"]])
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}
    return {"ok": True}


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
        if t.get("time_class") and t["time_class"] != "anytime":
            print(f"      when: {t['time_class']}")
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
    if getattr(args, "time_class", None) is not None:
        task["time_class"] = args.time_class
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


# --- Scheduling windows: one rule set, the agent picks a class per task ---
# Auto-scheduling fills any free gap on the calendar, but the task's time_class
# decides which gaps are acceptable. Waking bounds below cap "anytime"; working
# hours (from calendar preferences) pivot work_hours vs off_hours.
DAY_START_HOUR = 8
DAY_END_HOUR = 22
DEFAULT_WORK_START = 9
DEFAULT_WORK_END = 18
TIME_CLASSES = ("anytime", "work_hours", "off_hours")


def _class_bands(weekday: int, time_class: str, work_start: int, work_end: int) -> list[tuple[int, int]]:
    """Local-time (start_hour, end_hour) bands a time_class permits on a weekday.

    weekday: Mon=0 .. Sun=6.
    - anytime: any day, DAY_START..DAY_END (default for calls / flexible tasks)
    - work_hours: Mon-Fri work_start..work_end (needs businesses/offices open)
    - off_hours: weekday evenings work_end..DAY_END + full weekends (errands etc.)
    """
    is_weekend = weekday >= 5
    if time_class == "work_hours":
        return [] if is_weekend else [(work_start, work_end)]
    if time_class == "off_hours":
        if is_weekend:
            return [(DAY_START_HOUR, DAY_END_HOUR)]
        return [(work_end, DAY_END_HOUR)]
    return [(DAY_START_HOUR, DAY_END_HOUR)]  # anytime / unknown


def _allowed_subranges(slot_start, slot_end, time_class, work_start, work_end):
    """Intersect a free slot [slot_start, slot_end] (UTC) with the local-time
    bands permitted by time_class. Returns a list of (start, end) UTC ranges."""
    s_local = slot_start.astimezone(_shared.LOCAL_TZ)
    e_local = slot_end.astimezone(_shared.LOCAL_TZ)
    out = []
    day = s_local.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < e_local:
        for band_start, band_end in _class_bands(day.weekday(), time_class, work_start, work_end):
            lo = max(day.replace(hour=band_start, minute=0), s_local)
            hi = min(day.replace(hour=band_end, minute=0), e_local)
            if hi > lo:
                out.append((lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)))
        day += timedelta(days=1)
    return out


def _consume_slot(available, slot, ts, te, buffer):
    """Remove [ts-buffer, te+buffer] from `slot` within `available` (in place),
    keeping any free time before/after as separate slots."""
    idx = available.index(slot)
    s, e = slot["start"], slot["end"]
    available.pop(idx)
    leftovers = []
    if (ts - buffer) > s:
        leftovers.append({"start": s, "end": ts - buffer})
    if (te + buffer) < e:
        leftovers.append({"start": te + buffer, "end": e})
    for j, lo in enumerate(leftovers):
        available.insert(idx + j, lo)


def cmd_schedule_batch(args):
    """Batch-schedule pending tasks into free calendar slots."""
    _setup_calendar_imports()
    import calendar_client
    import scheduler as cal_scheduler

    prefs = cal_scheduler.load_preferences()
    CET = _shared.LOCAL_TZ

    # 1. Load pending tasks. Defensive: skip any task that already has a
    # calendar_event_id even if its status is a stale "pending" — never create
    # a second event for an already-linked task (the non-overlap invariant).
    tasks = load_tasks()
    pending = []
    for t in tasks:
        if t["status"] != "pending":
            continue
        if t.get("calendar_event_id"):
            print(f"Skipping already-linked task {t['id']} ({t['title']})", file=sys.stderr)
            continue
        pending.append(t)
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

    # 4. Parse free slots into datetime ranges. No window pre-filtering here —
    # every free gap is fair game; each task's time_class narrows it at
    # placement time (step 6), so the rule depends on the task, not the slot.
    work_start = prefs.get("work_start", DEFAULT_WORK_START)
    work_end = prefs.get("work_end", DEFAULT_WORK_END)
    available_slots = []
    for slot in raw_slots:
        try:
            s = datetime.fromisoformat(slot["start"])
            e = datetime.fromisoformat(slot["end"])
        except (ValueError, KeyError):
            continue
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        if e.tzinfo is None:
            e = e.replace(tzinfo=timezone.utc)
        if e > s:
            available_slots.append({"start": s, "end": e})

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
            time_class = task.get("time_class") or "anytime"
            if time_class not in TIME_CLASSES:
                time_class = "anytime"
            placed = False

            for slot in available_slots:
                # Only the sub-ranges of this free slot that the task's
                # time_class permits are candidates.
                subs = _allowed_subranges(slot["start"], slot["end"], time_class, work_start, work_end)
                for sub_start, sub_end in subs:
                    if sub_end - sub_start < duration:
                        continue
                    task_start = sub_start
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
                            placed = True  # already recorded; don't also count as unplaced
                            break

                    scheduled.append({
                        "task_id": task["id"],
                        "title": task["title"],
                        "time_class": time_class,
                        "start": task_start.astimezone(CET).strftime("%Y-%m-%d %H:%M"),
                        "end": task_end.astimezone(CET).strftime("%H:%M"),
                        "event_id": event_id,
                        "scheduled_at": task_start.astimezone(CET).isoformat(),
                    })

                    # Consume used time + buffer; keep free time before/after.
                    _consume_slot(available_slots, slot, task_start, task_end, buffer)
                    placed = True
                    break
                if placed:
                    break

            if not placed:
                task["carry_count"] = task.get("carry_count", 0) + 1
                info = {"task_id": task["id"], "title": task["title"],
                        "carry_count": task["carry_count"]}
                if task.get("deadline"):
                    info["deadline"] = task["deadline"]
                deferred.append(info)

    # 7. Update task statuses. Write back the calendar_event_id + scheduled_at
    # so batch-scheduled tasks are fully linked (editable/deletable from the
    # dashboard), exactly like the immediate --scheduled-at path.
    if not args.dry_run:
        all_tasks = load_tasks()
        scheduled_map = {s["task_id"]: s for s in scheduled}
        deferred_map = {d["task_id"]: d.get("carry_count") for d in deferred}
        for t in all_tasks:
            if t["id"] in scheduled_map:
                s = scheduled_map[t["id"]]
                t["status"] = "scheduled"
                t["scheduled_at"] = s["scheduled_at"]
                if s.get("event_id"):
                    t["calendar_event_id"] = s["event_id"]
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
    add_p.add_argument("--time-class", choices=TIME_CLASSES, default=None,
                       help="When auto-scheduling may place it: anytime (default), work_hours, off_hours")
    add_p.add_argument("--scheduled-at", default=None, help="Schedule on calendar: 'YYYY-MM-DD HH:MM' (Europe/Berlin)")

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
    update_p.add_argument("--time-class", choices=TIME_CLASSES, default=None)
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
    # Cross-process lock for the whole command: concurrent sub-agents (or
    # the bot's dashboard API) can't interleave a read-modify-write.
    with _shared.file_lock(DATA_FILE):
        cmds[args.command](args)


if __name__ == "__main__":
    main()
