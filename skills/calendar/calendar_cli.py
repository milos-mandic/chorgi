#!/usr/bin/env python3
"""CLI wrapper for calendar operations. Used by the calendar sub-agent via Bash."""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add skill directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent))
import calendar_client
import scheduler


def _parse_dt(s: str) -> datetime:
    """Parse a datetime string in common formats."""
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    # Try ISO format with timezone
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    raise ValueError(f"Cannot parse datetime: {s}")


def _resolve_calendar_id(which: str) -> str:
    """Resolve 'owner' or 'bot' to the actual calendar ID."""
    owner_id, bot_id = calendar_client._get_calendar_ids()
    if which == "owner":
        return owner_id
    return bot_id


def cmd_list(args):
    """List upcoming events."""
    cal_id = _resolve_calendar_id(args.calendar)
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=args.days)
    events = calendar_client.list_events(cal_id, now, time_max)
    if not events:
        print(f"No events in the next {args.days} day(s) on {args.calendar} calendar.")
    else:
        print(json.dumps(events, indent=2))


def cmd_free(args):
    """Find free time slots."""
    owner_id, bot_id = calendar_client._get_calendar_ids()
    now = datetime.now(timezone.utc)
    time_max = now + timedelta(days=args.days)
    slots = calendar_client.find_free_slots(
        owner_id, bot_id, now, time_max, args.duration
    )
    if not slots:
        print(f"No free slots of {args.duration} minutes in the next {args.days} day(s).")
    else:
        print(json.dumps(slots, indent=2))


def cmd_create(args):
    """Create an event on the bot calendar."""
    _, bot_id = calendar_client._get_calendar_ids()
    start = _parse_dt(args.start)
    end = _parse_dt(args.end)
    event = calendar_client.create_event(
        bot_id, args.title, start, end, description=args.description
    )
    print(json.dumps(event, indent=2))


def cmd_update(args):
    """Update an existing event."""
    _, bot_id = calendar_client._get_calendar_ids()
    updates = {}
    if args.title:
        updates["summary"] = args.title
    if args.start:
        updates["start"] = _parse_dt(args.start)
    if args.end:
        updates["end"] = _parse_dt(args.end)
    if args.description:
        updates["description"] = args.description
    if not updates:
        print("No updates specified.")
        return
    event = calendar_client.update_event(bot_id, args.event_id, **updates)
    print(json.dumps(event, indent=2))


def cmd_delete(args):
    """Delete an event."""
    _, bot_id = calendar_client._get_calendar_ids()
    result = calendar_client.delete_event(bot_id, args.event_id)
    print(result)


def cmd_suggest(args):
    """Suggest optimal time slots for a task."""
    suggestions = scheduler.suggest_slots(
        task_description=args.task,
        duration_minutes=args.duration,
        importance=args.importance,
        deadline=args.deadline,
        target_date=args.date,
        max_suggestions=3,
    )
    if not suggestions:
        print("No suitable time slots found.")
    else:
        print(json.dumps(suggestions, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Calendar CLI for chorgi")
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p = sub.add_parser("list", help="List upcoming events")
    p.add_argument("--days", type=int, default=7, help="Number of days to look ahead")
    p.add_argument("--calendar", choices=["owner", "bot"], default="owner",
                    help="Which calendar to query")
    p.set_defaults(func=cmd_list)

    # free
    p = sub.add_parser("free", help="Find free time slots")
    p.add_argument("--duration", type=int, default=60,
                    help="Required slot duration in minutes")
    p.add_argument("--days", type=int, default=7, help="Days to search ahead")
    p.set_defaults(func=cmd_free)

    # create
    p = sub.add_parser("create", help="Create event on bot calendar")
    p.add_argument("title", help="Event title")
    p.add_argument("start", help="Start time (YYYY-MM-DD HH:MM)")
    p.add_argument("end", help="End time (YYYY-MM-DD HH:MM)")
    p.add_argument("--description", help="Event description", default=None)
    p.set_defaults(func=cmd_create)

    # update
    p = sub.add_parser("update", help="Update an event")
    p.add_argument("event_id", help="Event ID to update")
    p.add_argument("--title", help="New title", default=None)
    p.add_argument("--start", help="New start time", default=None)
    p.add_argument("--end", help="New end time", default=None)
    p.add_argument("--description", help="New description", default=None)
    p.set_defaults(func=cmd_update)

    # delete
    p = sub.add_parser("delete", help="Delete an event")
    p.add_argument("event_id", help="Event ID to delete")
    p.set_defaults(func=cmd_delete)

    # suggest
    p = sub.add_parser("suggest", help="Suggest optimal time slot for a task")
    p.add_argument("task", help="Task description")
    p.add_argument("--duration", type=int, default=None,
                    help="Duration in minutes (default from preferences)")
    p.add_argument("--importance", choices=["high", "medium", "low"],
                    default="medium", help="Task importance")
    p.add_argument("--deadline", default=None,
                    help="Deadline date (YYYY-MM-DD)")
    p.add_argument("--date", default=None,
                    help="Target date to search (YYYY-MM-DD)")
    p.set_defaults(func=cmd_suggest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
