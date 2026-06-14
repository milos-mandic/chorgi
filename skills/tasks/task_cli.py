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
# or its output contract changes, --scheduled-at and free-slots break.
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
        if t.get("scheduled_at"):
            print(f"      scheduled: {t['scheduled_at']}")
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
    # If this task owns a calendar event, delete it too so "remove" cleans up
    # everywhere (the dashboard delete path already does this).
    removed_event = False
    if task.get("calendar_event_id"):
        result = delete_calendar_event_for_task(task)
        if result["ok"]:
            removed_event = True
        else:
            print(f"Warning: calendar event not deleted ({result.get('error')}).")
    tasks.remove(task)
    save_tasks(tasks)
    print(f"Removed: {title}")
    if removed_event:
        print("Calendar event deleted.")


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

    scheduled_at = getattr(args, "scheduled_at", None)
    if scheduled_at:
        if task.get("calendar_event_id"):
            result = update_calendar_event_for_task(task, scheduled_at)
        else:
            result = create_calendar_event_for_task(task, scheduled_at)
        if result["ok"]:
            task["scheduled_at"] = result["scheduled_at"]
            task["status"] = "scheduled"
            if "event_id" in result:
                task["calendar_event_id"] = result["event_id"]
        else:
            print(f"Calendar update failed: {result.get('error')}")

    save_tasks(tasks)
    print(f"Updated: {task['title']} [{task['id']}]")
    if scheduled_at and task.get("scheduled_at"):
        from datetime import datetime
        dt = datetime.fromisoformat(task["scheduled_at"])
        print(f"Rescheduled to: {dt.strftime('%A %b %d at %-I:%M %p')}")


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


def cmd_free_slots(args):
    """List free calendar gaps in a date range as JSON.

    Used by the agent to resolve a vague request ("sometime next week") into a
    concrete time before booking with `add --scheduled-at`, so we never blind-
    force an event onto a busy slot. Scheduling itself stays in this skill.
    """
    _setup_calendar_imports()
    import calendar_client

    owner_id, bot_id = calendar_client._get_calendar_ids()
    start = _parse_scheduled_at(args.start + " 00:00") if len(args.start) <= 10 else _parse_scheduled_at(args.start)
    if args.end:
        end = _parse_scheduled_at(args.end + " 23:59") if len(args.end) <= 10 else _parse_scheduled_at(args.end)
    else:
        end = start + timedelta(days=7)

    raw_slots = calendar_client.find_free_slots(
        owner_id, bot_id,
        start.astimezone(timezone.utc), end.astimezone(timezone.utc),
        duration_minutes=args.duration,
    )

    slots = []
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
        s_local = s.astimezone(_shared.LOCAL_TZ)
        e_local = e.astimezone(_shared.LOCAL_TZ)
        slots.append({
            "start": s_local.strftime("%Y-%m-%d %H:%M"),
            "end": e_local.strftime("%Y-%m-%d %H:%M"),
            "day": s_local.strftime("%A"),
        })

    print(json.dumps({
        "range": {"start": start.strftime("%Y-%m-%d %H:%M"), "end": end.strftime("%Y-%m-%d %H:%M")},
        "min_duration_minutes": args.duration,
        "free_slots": slots,
    }, indent=2))


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
    update_p.add_argument("--carry-count", type=int, default=None)
    update_p.add_argument("--scheduled-at", default=None, help="Reschedule: 'YYYY-MM-DD HH:MM' (Europe/Berlin)")

    sub.add_parser("pending-json", help="Dump pending tasks as JSON")
    sub.add_parser("clear-done", help="Remove all completed tasks")

    free_p = sub.add_parser("free-slots", help="List free calendar gaps in a date range (to pick a time)")
    free_p.add_argument("--start", required=True, help="Range start: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' (Europe/Berlin)")
    free_p.add_argument("--end", default=None, help="Range end (default: 7 days after start)")
    free_p.add_argument("--duration", type=int, default=60, help="Minimum slot length in minutes (default 60)")

    args = parser.parse_args()
    cmds = {
        "add": cmd_add,
        "list": cmd_list,
        "done": cmd_done,
        "remove": cmd_remove,
        "update": cmd_update,
        "pending-json": cmd_pending_json,
        "clear-done": cmd_clear_done,
        "free-slots": cmd_free_slots,
    }
    # Cross-process lock for the whole command: concurrent sub-agents (or
    # the bot's dashboard API) can't interleave a read-modify-write.
    with _shared.file_lock(DATA_FILE):
        cmds[args.command](args)


if __name__ == "__main__":
    main()
