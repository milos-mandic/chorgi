#!/usr/bin/env python3
"""MCP tool server for calendar operations. Wraps calendar_client.py directly."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add skill directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent))

# Load secrets.env if env vars aren't already set
_SECRETS_PATH = Path(__file__).resolve().parent.parent.parent / ".personal" / "secrets.env"
if not os.environ.get("CALENDAR_OWNER_ID") and _SECRETS_PATH.exists():
    for _line in _SECRETS_PATH.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))

import calendar_client
import scheduler
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("calendar")


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


@mcp.tool()
def list_events(days: int = 7, calendar: str = "owner") -> str:
    """List upcoming calendar events.

    Args:
        days: Number of days to look ahead (default 7)
        calendar: Which calendar to query — "owner" for user's personal calendar, "bot" for chorgibot calendar
    """
    try:
        cal_id = _resolve_calendar_id(calendar)
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days)
        events = calendar_client.list_events(cal_id, now, time_max)
        if not events:
            return f"No events in the next {days} day(s) on {calendar} calendar."
        return json.dumps(events, indent=2)
    except Exception as e:
        return f"Error listing events: {e}"


@mcp.tool()
def find_free_slots(duration_minutes: int = 60, days: int = 7) -> str:
    """Find available time slots across both owner and bot calendars.

    Args:
        duration_minutes: Required slot duration in minutes (default 60)
        days: Number of days to search ahead (default 7)
    """
    try:
        owner_id, bot_id = calendar_client._get_calendar_ids()
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days)
        slots = calendar_client.find_free_slots(owner_id, bot_id, now, time_max, duration_minutes)
        if not slots:
            return f"No free slots of {duration_minutes} minutes in the next {days} day(s)."
        return json.dumps(slots, indent=2)
    except Exception as e:
        return f"Error finding free slots: {e}"


@mcp.tool()
def create_event(
    title: str,
    start: str,
    end: str,
    description: str = "",
    attendees: str = "",
    invite_owner: bool = True,
    force: bool = False,
) -> str:
    """Create an event on the bot calendar (chorgibot@gmail.com).

    Automatically checks both calendars for conflicts before creating.
    The calendar owner is invited by default so they receive a Google Calendar invite.

    Args:
        title: Event title
        start: Start time in "YYYY-MM-DD HH:MM" format (UTC)
        end: End time in "YYYY-MM-DD HH:MM" format (UTC)
        description: Optional event description
        attendees: Optional comma-separated email addresses to invite
        invite_owner: Auto-add calendar owner as attendee (default True)
        force: Create even if there are time conflicts (default False)
    """
    try:
        owner_id, bot_id = calendar_client._get_calendar_ids()
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end)

        # Conflict check
        if not force:
            conflicts = calendar_client.check_conflicts(owner_id, bot_id, start_dt, end_dt)
            if conflicts:
                conflict_info = [
                    {"summary": c["summary"], "start": c["start"], "end": c["end"]}
                    for c in conflicts
                ]
                return json.dumps({
                    "error": "Time conflict detected — cannot create event during busy time.",
                    "conflicts": conflict_info,
                    "hint": "Use force=True to override, or choose a different time.",
                }, indent=2)

        attendee_list = []
        if attendees:
            attendee_list.extend(e.strip() for e in attendees.split(",") if e.strip())
        if invite_owner:
            if owner_id not in attendee_list:
                attendee_list.append(owner_id)

        event = calendar_client.create_event(
            bot_id, title, start_dt, end_dt,
            description=description or None,
            attendees=attendee_list or None,
        )

        invite_status = event.get("invite_status", "")
        if invite_status == "sent":
            event["note"] = "Calendar invite sent to all attendees."
        elif invite_status == "no_email":
            event["note"] = "Event created with attendees listed, but invite emails could not be sent."
        elif invite_status == "no_attendees":
            event["note"] = "Event created, but attendees could not be added."

        return json.dumps(event, indent=2)
    except Exception as e:
        return f"Error creating event: {e}"


@mcp.tool()
def update_event(
    event_id: str,
    title: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
) -> str:
    """Update an existing event on the bot calendar.

    Args:
        event_id: The event ID to update
        title: New title (leave empty to keep current)
        start: New start time in "YYYY-MM-DD HH:MM" format (leave empty to keep current)
        end: New end time in "YYYY-MM-DD HH:MM" format (leave empty to keep current)
        description: New description (leave empty to keep current)
    """
    try:
        _, bot_id = calendar_client._get_calendar_ids()
        updates = {}
        if title:
            updates["summary"] = title
        if start:
            updates["start"] = _parse_dt(start)
        if end:
            updates["end"] = _parse_dt(end)
        if description:
            updates["description"] = description
        if not updates:
            return "No updates specified."
        event = calendar_client.update_event(bot_id, event_id, **updates)
        return json.dumps(event, indent=2)
    except Exception as e:
        return f"Error updating event: {e}"


@mcp.tool()
def delete_event(event_id: str) -> str:
    """Delete an event from the bot calendar.

    Args:
        event_id: The event ID to delete
    """
    try:
        _, bot_id = calendar_client._get_calendar_ids()
        return calendar_client.delete_event(bot_id, event_id)
    except Exception as e:
        return f"Error deleting event: {e}"


@mcp.tool()
def suggest_slots(
    task: str,
    duration_minutes: int = 0,
    importance: str = "medium",
    deadline: str = "",
    target_date: str = "",
) -> str:
    """Suggest optimal time slots for a task based on availability and preferences.

    Considers task type (deep work → morning, meetings → afternoon), importance,
    deadline proximity, and user preferences from preferences.md.

    Args:
        task: Description of the task to schedule
        duration_minutes: Duration in minutes (0 = use default from preferences)
        importance: Task importance — "high", "medium", or "low"
        deadline: Optional deadline date in "YYYY-MM-DD" format
        target_date: Optional specific date to search in "YYYY-MM-DD" format
    """
    try:
        suggestions = scheduler.suggest_slots(
            task_description=task,
            duration_minutes=duration_minutes if duration_minutes > 0 else None,
            importance=importance,
            deadline=deadline or None,
            target_date=target_date or None,
            max_suggestions=3,
        )
        if not suggestions:
            return "No suitable time slots found."
        return json.dumps(suggestions, indent=2)
    except Exception as e:
        return f"Error suggesting slots: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
