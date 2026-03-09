"""Google Calendar API client using OAuth2 user credentials."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Default paths — can be overridden via env vars
_SKILL_DIR = Path(__file__).parent
_DEFAULT_CREDENTIALS = _SKILL_DIR / "oauth_credentials.json"
_DEFAULT_TOKEN = _SKILL_DIR / "token.json"

# Cache built service
_service = None


def _resolve_path(env_var: str, default: Path) -> str:
    """Resolve a path from env var or default, handling relative paths."""
    val = os.environ.get(env_var)
    if val is None:
        return str(default)
    p = Path(val)
    if not p.is_absolute():
        # Resolve relative to project root (2 levels up from skill dir)
        p = _SKILL_DIR.parent.parent / p
    return str(p)


def _get_credentials_path() -> str:
    return _resolve_path("GOOGLE_OAUTH_CREDENTIALS", _DEFAULT_CREDENTIALS)


def _get_token_path() -> str:
    return _resolve_path("GOOGLE_OAUTH_TOKEN", _DEFAULT_TOKEN)


def _get_calendar_ids() -> tuple[str, str]:
    """Return (owner_calendar_id, bot_calendar_id) from env vars."""
    owner = os.environ.get("CALENDAR_OWNER_ID", "")
    bot = os.environ.get("CALENDAR_BOT_ID", "")
    if not owner or not bot:
        raise RuntimeError(
            "CALENDAR_OWNER_ID and CALENDAR_BOT_ID must be set in secrets.env"
        )
    return owner, bot


def _load_credentials() -> Credentials:
    """Load OAuth2 credentials, refreshing or re-authing as needed."""
    token_path = _get_token_path()
    creds = None

    # Load existing token
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Refresh or re-auth
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            log.warning("Token refresh failed: %s — re-authenticating", e)
            creds = None

    if not creds or not creds.valid:
        creds_path = _get_credentials_path()
        if not os.path.exists(creds_path):
            raise RuntimeError(
                f"OAuth credentials not found: {creds_path}\n"
                "Download OAuth client credentials from GCP console:\n"
                "  APIs & Services → Credentials → Create OAuth client ID → Desktop app\n"
                "Save the JSON file as oauth_credentials.json in the calendar skill directory."
            )
        flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
        creds = flow.run_local_server(
            port=8085, open_browser=False,
            authorization_prompt_message="\nOpen this URL in Chrome to authorize:\n{url}\n",
        )

    # Save token for next time
    with open(token_path, "w") as f:
        f.write(creds.to_json())

    return creds


def get_service():
    """Build and cache an authenticated Calendar API service."""
    global _service
    if _service is not None:
        return _service

    creds = _load_credentials()
    _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


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
    attendees: list[str] | None = None,
) -> dict:
    """Create an event on the specified calendar.

    With OAuth2 user credentials, attendee invites work directly —
    Google sends invite emails as the authenticated user.
    Returns an invite_status field: "sent" or "no_attendees".
    """
    service = get_service()
    body = {
        "summary": summary,
        "start": _dt_to_gcal(start),
        "end": _dt_to_gcal(end),
    }
    if description:
        body["description"] = description

    send_updates = "none"
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees]
        send_updates = "all"

    event = service.events().insert(
        calendarId=calendar_id, body=body, sendUpdates=send_updates
    ).execute()
    result = _event_to_dict(event)
    result["invite_status"] = "sent" if attendees else "no_attendees"
    return result


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


def check_conflicts(
    owner_cal_id: str,
    bot_cal_id: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Check if a time range conflicts with existing events on either calendar.

    Returns list of conflicting events (empty if time is free).
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    conflicts = []
    for cal_id in (owner_cal_id, bot_cal_id):
        events = list_events(cal_id, start, end, max_results=50)
        for e in events:
            ev_start = e.get("start", "")
            ev_end = e.get("end", "")
            if not ev_start or not ev_end:
                continue
            try:
                es = datetime.fromisoformat(ev_start)
                ee = datetime.fromisoformat(ev_end)
                if es.tzinfo is None:
                    es = es.replace(tzinfo=timezone.utc)
                if ee.tzinfo is None:
                    ee = ee.replace(tzinfo=timezone.utc)
                if es < end and ee > start:
                    conflicts.append(e)
            except (ValueError, TypeError):
                continue

    return conflicts


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
