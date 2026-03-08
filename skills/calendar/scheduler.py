"""Smart scheduling logic — scores time slots based on preferences and availability."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import calendar_client

PREFERENCES_PATH = Path(__file__).parent / "preferences.md"

# Keyword → preferred time-of-day mapping
TASK_TYPE_KEYWORDS = {
    "morning": [
        "deep work", "coding", "code", "creative", "write", "writing",
        "design", "focus", "research", "study", "review pr", "debug",
    ],
    "afternoon": [
        "meeting", "call", "sync", "standup", "1:1", "one-on-one",
        "interview", "presentation", "demo", "chat",
    ],
    "end_of_day": [
        "admin", "email", "emails", "light", "cleanup", "organize",
        "plan", "planning", "paperwork", "expense",
    ],
}


def load_preferences() -> dict:
    """Parse preferences.md into a structured dict."""
    prefs = {
        "work_start": 9,
        "work_end": 18,
        "weekends_off": True,
        "default_duration": 60,
        "buffer_minutes": 15,
        "default_importance": "medium",
        "horizon_days": 14,
        "morning_start": 9,
        "morning_end": 12,
        "afternoon_start": 13,
        "afternoon_end": 16,
        "eod_start": 16,
        "eod_end": 18,
    }

    if not PREFERENCES_PATH.exists():
        return prefs

    text = PREFERENCES_PATH.read_text()

    # Parse working hours
    m = re.search(r"Weekdays:\s*(\d+):(\d+)\s*(AM|PM)\s*-\s*(\d+):(\d+)\s*(AM|PM)", text, re.IGNORECASE)
    if m:
        prefs["work_start"] = _to_24h(int(m.group(1)), m.group(3))
        prefs["work_end"] = _to_24h(int(m.group(4)), m.group(6))

    if re.search(r"Weekends:\s*Off", text, re.IGNORECASE):
        prefs["weekends_off"] = True

    # Parse defaults
    m = re.search(r"Default task duration:\s*(\d+)", text)
    if m:
        prefs["default_duration"] = int(m.group(1))
    m = re.search(r"Buffer between events:\s*(\d+)", text)
    if m:
        prefs["buffer_minutes"] = int(m.group(1))
    m = re.search(r"Scheduling horizon.*?:\s*(?:next\s+)?(\d+)\s*days", text, re.IGNORECASE)
    if m:
        prefs["horizon_days"] = int(m.group(1))

    return prefs


def _to_24h(hour: int, ampm: str) -> int:
    ampm = ampm.upper()
    if ampm == "AM" and hour == 12:
        return 0
    if ampm == "PM" and hour != 12:
        return hour + 12
    return hour


def classify_task_type(task_description: str) -> str | None:
    """Determine preferred time-of-day based on task keywords."""
    lower = task_description.lower()
    for time_slot, keywords in TASK_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return time_slot
    return None


def score_slot(
    slot_start: datetime,
    slot_end: datetime,
    prefs: dict,
    task_type: str | None = None,
    importance: str = "medium",
    deadline: datetime | None = None,
    now: datetime | None = None,
) -> float:
    """Score a time slot (0-100). Higher is better."""
    if now is None:
        now = datetime.now(timezone.utc)

    score = 50.0  # base
    hour = slot_start.hour
    weekday = slot_start.weekday()  # 0=Mon, 6=Sun

    # Weekend penalty
    if prefs.get("weekends_off", True) and weekday >= 5:
        score -= 40

    # Working hours bonus
    if prefs["work_start"] <= hour < prefs["work_end"]:
        score += 15
    else:
        score -= 25

    # Task type alignment
    if task_type == "morning" and prefs["morning_start"] <= hour < prefs["morning_end"]:
        score += 20
    elif task_type == "afternoon" and prefs["afternoon_start"] <= hour < prefs["afternoon_end"]:
        score += 20
    elif task_type == "end_of_day" and prefs["eod_start"] <= hour < prefs["eod_end"]:
        score += 20
    elif task_type is not None:
        # Wrong time-of-day for task type
        score -= 10

    # Importance weighting — high importance prefers earlier slots
    if importance == "high":
        days_out = (slot_start - now).total_seconds() / 86400
        score += max(0, 15 - days_out * 2)
    elif importance == "low":
        score += 5  # slight bonus for any slot (flexible)

    # Deadline proximity — prefer slots well before deadline
    if deadline:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        days_to_deadline = (deadline - slot_start).total_seconds() / 86400
        if days_to_deadline < 0:
            score -= 50  # past deadline
        elif days_to_deadline < 1:
            score += 10  # urgent, schedule soon
        elif days_to_deadline < 3:
            score += 5

    # Prefer earlier in the day within preferred windows
    if prefs["work_start"] <= hour < prefs["work_end"]:
        score += (prefs["work_end"] - hour) * 0.5

    return max(0, min(100, score))


def suggest_slots(
    task_description: str,
    duration_minutes: int | None = None,
    importance: str = "medium",
    deadline: str | None = None,
    target_date: str | None = None,
    max_suggestions: int = 3,
) -> list[dict]:
    """Find and rank optimal time slots for a task.

    Args:
        task_description: What the task is (used for type classification)
        duration_minutes: How long the task needs (defaults to preference)
        importance: high, medium, or low
        deadline: Optional deadline as ISO date string
        target_date: Optional specific date to search (YYYY-MM-DD)
        max_suggestions: Number of suggestions to return

    Returns:
        List of {start, end, score, reason} dicts, sorted by score desc.
    """
    prefs = load_preferences()
    if duration_minutes is None:
        duration_minutes = prefs["default_duration"]

    owner_id, bot_id = calendar_client._get_calendar_ids()
    now = datetime.now(timezone.utc)
    buffer = timedelta(minutes=prefs["buffer_minutes"])

    # Determine search window
    if target_date:
        try:
            day = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            day = now
        time_min = day.replace(hour=prefs["work_start"], minute=0, second=0)
        time_max = day.replace(hour=prefs["work_end"], minute=0, second=0)
        if time_min < now:
            time_min = now
    else:
        time_min = now
        time_max = now + timedelta(days=prefs["horizon_days"])

    # Parse deadline
    deadline_dt = None
    if deadline:
        try:
            deadline_dt = datetime.strptime(deadline, "%Y-%m-%d").replace(
                hour=23, minute=59, tzinfo=timezone.utc
            )
        except ValueError:
            pass

    # Get free slots
    free_slots = calendar_client.find_free_slots(
        owner_id, bot_id, time_min, time_max, duration_minutes
    )

    # Classify task type
    task_type = classify_task_type(task_description)

    # Generate candidate slots from free periods
    candidates = []
    duration = timedelta(minutes=duration_minutes)

    for slot in free_slots:
        try:
            slot_start = datetime.fromisoformat(slot["start"])
            slot_end = datetime.fromisoformat(slot["end"])
        except (ValueError, KeyError):
            continue

        if slot_start.tzinfo is None:
            slot_start = slot_start.replace(tzinfo=timezone.utc)
        if slot_end.tzinfo is None:
            slot_end = slot_end.replace(tzinfo=timezone.utc)

        # Apply buffer after previous event
        slot_start = slot_start + buffer

        # Generate hourly-aligned candidates within this free period
        cursor = slot_start.replace(minute=0, second=0, microsecond=0)
        if cursor < slot_start:
            cursor += timedelta(hours=1)

        while cursor + duration <= slot_end - buffer:
            # Skip outside working hours
            if prefs["work_start"] <= cursor.hour < prefs["work_end"]:
                # Skip weekends if preference set
                if not (prefs.get("weekends_off", True) and cursor.weekday() >= 5):
                    s = score_slot(
                        cursor, cursor + duration, prefs,
                        task_type=task_type,
                        importance=importance,
                        deadline=deadline_dt,
                        now=now,
                    )
                    candidates.append({
                        "start": cursor.isoformat(),
                        "end": (cursor + duration).isoformat(),
                        "score": round(s, 1),
                        "reason": _build_reason(cursor, task_type, importance, prefs),
                    })
            cursor += timedelta(hours=1)

    # Also try half-hour offsets for the top free slots (finer granularity)
    for slot in free_slots[:5]:
        try:
            slot_start = datetime.fromisoformat(slot["start"]) + buffer
            slot_end = datetime.fromisoformat(slot["end"])
        except (ValueError, KeyError):
            continue

        if slot_start.tzinfo is None:
            slot_start = slot_start.replace(tzinfo=timezone.utc)
        if slot_end.tzinfo is None:
            slot_end = slot_end.replace(tzinfo=timezone.utc)

        cursor = slot_start.replace(minute=30, second=0, microsecond=0)
        if cursor < slot_start:
            cursor += timedelta(hours=1)

        while cursor + duration <= slot_end - buffer:
            if prefs["work_start"] <= cursor.hour < prefs["work_end"]:
                if not (prefs.get("weekends_off", True) and cursor.weekday() >= 5):
                    s = score_slot(
                        cursor, cursor + duration, prefs,
                        task_type=task_type,
                        importance=importance,
                        deadline=deadline_dt,
                        now=now,
                    )
                    candidates.append({
                        "start": cursor.isoformat(),
                        "end": (cursor + duration).isoformat(),
                        "score": round(s, 1),
                        "reason": _build_reason(cursor, task_type, importance, prefs),
                    })
            cursor += timedelta(hours=1)

    # Deduplicate and sort by score
    seen = set()
    unique = []
    for c in candidates:
        key = c["start"]
        if key not in seen:
            seen.add(key)
            unique.append(c)

    unique.sort(key=lambda x: x["score"], reverse=True)
    return unique[:max_suggestions]


def _build_reason(
    slot_start: datetime,
    task_type: str | None,
    importance: str,
    prefs: dict,
) -> str:
    """Build a human-readable reason for why this slot was chosen."""
    parts = []
    hour = slot_start.hour
    day_name = slot_start.strftime("%A")

    if task_type == "morning" and prefs["morning_start"] <= hour < prefs["morning_end"]:
        parts.append("morning slot matches deep work preference")
    elif task_type == "afternoon" and prefs["afternoon_start"] <= hour < prefs["afternoon_end"]:
        parts.append("afternoon slot matches meeting preference")
    elif task_type == "end_of_day" and prefs["eod_start"] <= hour < prefs["eod_end"]:
        parts.append("end-of-day slot matches admin task preference")
    else:
        parts.append(f"{day_name} {slot_start.strftime('%I:%M %p')}")

    if importance == "high":
        parts.append("scheduled soon (high priority)")
    elif importance == "low":
        parts.append("flexible timing")

    if prefs["work_start"] <= hour < prefs["work_end"]:
        parts.append("within working hours")

    return "; ".join(parts)
