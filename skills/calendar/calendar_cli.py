#!/usr/bin/env python3
"""CLI wrapper for calendar operations. Used by the calendar sub-agent via Bash."""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add skill directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

# Load secrets.env if env vars aren't already set (direct CLI invocation)
_SECRETS_PATH = Path(__file__).resolve().parent.parent.parent / ".personal" / "secrets.env"
if not os.environ.get("CALENDAR_OWNER_ID") and _SECRETS_PATH.exists():
    for _line in _SECRETS_PATH.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

import calendar_client
import scheduler


# --- Output helpers ---

def _output(data):
    """Print structured JSON result with metadata footer."""
    t0 = _output._start_time
    elapsed = int((time.time() - t0) * 1000)
    print(json.dumps(data, indent=2))
    print(f"[exit:0 | {elapsed}ms]", file=sys.stderr)

_output._start_time = time.time()


def _error(msg, hint=None):
    """Print structured error JSON with metadata footer and exit."""
    t0 = _output._start_time
    elapsed = int((time.time() - t0) * 1000)
    err = {"error": msg}
    if hint:
        err["hint"] = hint
    print(json.dumps(err, indent=2))
    print(f"[exit:1 | {elapsed}ms]", file=sys.stderr)
    sys.exit(1)


class NavigationalParser(argparse.ArgumentParser):
    """Argparse subclass that emits JSON errors with usage hints."""

    def error(self, message):
        _error(message, hint=f"Run: python calendar_cli.py {self.prog.split()[-1] if ' ' in self.prog else ''} --help".strip())


# --- Helpers ---

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


# --- Commands ---

def cmd_list(args):
    """List upcoming events."""
    try:
        cal_id = _resolve_calendar_id(args.calendar)
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=args.days)
        events = calendar_client.list_events(cal_id, now, time_max)
        if not events:
            _output({"events": [], "message": f"No events in the next {args.days} day(s) on {args.calendar} calendar."})
        else:
            _output({"events": events, "count": len(events)})
    except Exception as e:
        _error(str(e), hint="Check that CALENDAR_OWNER_ID and CALENDAR_BOT_ID are set, and token.json exists.")


def cmd_free(args):
    """Find free time slots."""
    try:
        owner_id, bot_id = calendar_client._get_calendar_ids()
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=args.days)
        slots = calendar_client.find_free_slots(
            owner_id, bot_id, now, time_max, args.duration
        )
        if not slots:
            _output({"slots": [], "message": f"No free slots of {args.duration} minutes in the next {args.days} day(s)."})
        else:
            _output({"slots": slots, "count": len(slots)})
    except Exception as e:
        _error(str(e), hint="Check that calendar credentials are configured.")


def cmd_create(args):
    """Create an event on the bot calendar."""
    try:
        owner_id, bot_id = calendar_client._get_calendar_ids()
        start = _parse_dt(args.start)
        end = _parse_dt(args.end)

        # Enforce availability check — refuse to create on busy time
        if not args.force:
            conflicts = calendar_client.check_conflicts(owner_id, bot_id, start, end)
            if conflicts:
                conflict_info = [
                    {"summary": c["summary"], "start": c["start"], "end": c["end"]}
                    for c in conflicts
                ]
                _error(
                    "Time conflict detected — cannot create event during busy time.",
                    hint="Use --force to override, or choose a different time."
                )

        attendees = []
        if args.attendees:
            attendees.extend(e.strip() for e in args.attendees.split(",") if e.strip())
        if not args.no_invite_owner:
            if owner_id not in attendees:
                attendees.append(owner_id)
        event = calendar_client.create_event(
            bot_id, args.title, start, end,
            description=args.description,
            attendees=attendees or None,
        )
        invite_status = event.get("invite_status", "")
        if invite_status == "sent":
            event["note"] = "Calendar invite sent to all attendees."
        elif invite_status == "no_email":
            event["note"] = "Event created with attendees listed, but invite emails could not be sent."
        elif invite_status == "no_attendees":
            event["note"] = "Event created, but attendees could not be added. Check the calendar link to add them manually."
        _output(event)
    except SystemExit:
        raise
    except Exception as e:
        _error(str(e), hint="Check arguments: create \"Title\" \"YYYY-MM-DD HH:MM\" \"YYYY-MM-DD HH:MM\"")


def cmd_update(args):
    """Update an existing event."""
    try:
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
            _error("No updates specified.", hint="Provide at least one of: --title, --start, --end, --description")
        event = calendar_client.update_event(bot_id, args.event_id, **updates)
        _output(event)
    except SystemExit:
        raise
    except Exception as e:
        _error(str(e), hint="Check that the event_id is valid. Use: python calendar_cli.py list --calendar bot")


def cmd_delete(args):
    """Delete an event."""
    try:
        _, bot_id = calendar_client._get_calendar_ids()
        result = calendar_client.delete_event(bot_id, args.event_id)
        _output({"deleted": args.event_id, "message": str(result)})
    except Exception as e:
        _error(str(e), hint="Check that the event_id is valid. Use: python calendar_cli.py list --calendar bot")


def cmd_suggest(args):
    """Suggest optimal time slots for a task."""
    try:
        suggestions = scheduler.suggest_slots(
            task_description=args.task,
            duration_minutes=args.duration,
            importance=args.importance,
            deadline=args.deadline,
            target_date=args.date,
            max_suggestions=3,
        )
        if not suggestions:
            _output({"suggestions": [], "message": "No suitable time slots found."})
        else:
            _output({"suggestions": suggestions, "count": len(suggestions)})
    except Exception as e:
        _error(str(e), hint="Check that calendar credentials are configured and preferences.md exists.")


def main():
    _output._start_time = time.time()

    parser = NavigationalParser(description="Calendar CLI for chorgi")
    sub = parser.add_subparsers(dest="command")

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
    p.add_argument("--attendees", help="Comma-separated email addresses to invite",
                    default=None)
    p.add_argument("--no-invite-owner", action="store_true",
                    help="Don't auto-add calendar owner as attendee")
    p.add_argument("--force", action="store_true",
                    help="Create even if there are conflicting events")
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
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
