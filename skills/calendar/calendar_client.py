"""Google Calendar API client using service account authentication."""

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Cache built services to avoid re-auth on every call
_service_cache = {}


def _get_service_account_path() -> str:
    """Return path to service account JSON key file."""
    path = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        str(Path(__file__).parent / "service_account.json"),
    )
    if not os.path.exists(path):
        raise RuntimeError(
            f"Service account key not found: {path}\n"
            "Download it from GCP console and place it at the path above."
        )
    return path


def _get_calendar_ids() -> tuple[str, str]:
    """Return (owner_calendar_id, bot_calendar_id) from env vars."""
    owner = os.environ.get("CALENDAR_OWNER_ID", "")
    bot = os.environ.get("CALENDAR_BOT_ID", "")
    if not owner or not bot:
        raise RuntimeError(
            "CALENDAR_OWNER_ID and CALENDAR_BOT_ID must be set in secrets.env"
        )
    return owner, bot


def get_service():
    """Build and cache an authenticated Calendar API service."""
    if "calendar" in _service_cache:
        return _service_cache["calendar"]

    sa_path = _get_service_account_path()
    credentials = service_account.Credentials.from_service_account_file(
        sa_path, scopes=SCOPES
    )
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    _service_cache["calendar"] = service
    return service


def list_events(
    calendar_id: str,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    max_results: int = 50,
) -> list[dict]:
    """Fetch events from a calendar within a time range."""
    service = get_service()
    now = datetime.now(timezone.utc)
    if time_min is None:
        time_min = now
    if time_max is None:
        time_max = now + timedelta(days=7)

    # Ensure timezone-aware
    if time_min.tzinfo is None:
        time_min = time_min.replace(tzinfo=timezone.utc)
    if time_max.tzinfo is None:
        time_max = time_max.replace(tzinfo=timezone.utc)

    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    events = result.get("items", [])
    return [_event_to_dict(e) for e in events]


def create_event(
    calendar_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    description: str | None = None,
) -> dict:
    """Create an event on the specified calendar."""
    service = get_service()
    body = {
        "summary": summary,
        "start": _dt_to_gcal(start),
        "end": _dt_to_gcal(end),
    }
    if description:
        body["description"] = description

    event = service.events().insert(calendarId=calendar_id, body=body).execute()
    return _event_to_dict(event)


def update_event(calendar_id: str, event_id: str, **updates) -> dict:
    """Update an existing event. Supported keys: summary, start, end, description."""
    service = get_service()
    # Fetch current event
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

    if "summary" in updates:
        event["summary"] = updates["summary"]
    if "description" in updates:
        event["description"] = updates["description"]
    if "start" in updates:
        event["start"] = _dt_to_gcal(updates["start"])
    if "end" in updates:
        event["end"] = _dt_to_gcal(updates["end"])

    updated = (
        service.events()
        .update(calendarId=calendar_id, eventId=event_id, body=event)
        .execute()
    )
    return _event_to_dict(updated)


def delete_event(calendar_id: str, event_id: str) -> str:
    """Delete an event from the specified calendar."""
    service = get_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    return f"Event {event_id} deleted."


def find_free_slots(
    owner_cal_id: str,
    bot_cal_id: str,
    time_min: datetime,
    time_max: datetime,
    duration_minutes: int = 60,
) -> list[dict]:
    """Find available time slots across both calendars.

    Returns list of {start, end} dicts representing free slots of at
    least `duration_minutes` length.
    """
    # Gather all events from both calendars
    owner_events = list_events(owner_cal_id, time_min, time_max, max_results=200)
    bot_events = list_events(bot_cal_id, time_min, time_max, max_results=200)

    # Merge and sort all busy periods
    busy = []
    for e in owner_events + bot_events:
        start_str = e.get("start", "")
        end_str = e.get("end", "")
        if not start_str or not end_str:
            continue
        try:
            s = datetime.fromisoformat(start_str)
            en = datetime.fromisoformat(end_str)
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if en.tzinfo is None:
                en = en.replace(tzinfo=timezone.utc)
            busy.append((s, en))
        except (ValueError, TypeError):
            continue

    busy.sort(key=lambda x: x[0])

    # Merge overlapping intervals
    merged = []
    for start, end in busy:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Find gaps
    duration = timedelta(minutes=duration_minutes)
    if time_min.tzinfo is None:
        time_min = time_min.replace(tzinfo=timezone.utc)
    if time_max.tzinfo is None:
        time_max = time_max.replace(tzinfo=timezone.utc)

    free = []
    cursor = time_min

    for busy_start, busy_end in merged:
        if cursor + duration <= busy_start:
            free.append({
                "start": cursor.isoformat(),
                "end": busy_start.isoformat(),
            })
        cursor = max(cursor, busy_end)

    # Trailing free time
    if cursor + duration <= time_max:
        free.append({
            "start": cursor.isoformat(),
            "end": time_max.isoformat(),
        })

    return free


def _dt_to_gcal(dt: datetime) -> dict:
    """Convert datetime to Google Calendar API format."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return {"dateTime": dt.isoformat(), "timeZone": str(dt.tzinfo)}


def _event_to_dict(event: dict) -> dict:
    """Normalize a Google Calendar event to a simple dict."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id", ""),
        "summary": event.get("summary", "(no title)"),
        "start": start.get("dateTime", start.get("date", "")),
        "end": end.get("dateTime", end.get("date", "")),
        "description": event.get("description", ""),
        "html_link": event.get("htmlLink", ""),
    }
