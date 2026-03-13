"""Fathom transcript library — parsing, formatting, storage, search."""

import re
import time
from pathlib import Path

WORKSPACE = Path(__file__).parent / "workspace"


def parse_fathom_payload(data: dict) -> dict | None:
    """Extract structured transcript data from a Fathom webhook payload.

    Returns dict with title, date, speakers, entries — or None if no transcript.
    """
    transcript = data.get("transcript")
    if not transcript:
        return None

    title = data.get("title", "Untitled Meeting")

    date_str = data.get("created_at") or data.get("recording_start_time") or ""
    date_prefix = date_str[:10] if date_str else time.strftime("%Y-%m-%d")

    speakers = []
    seen = set()
    entries = []
    for entry in transcript:
        name = entry.get("speaker", {}).get("display_name", "Unknown")
        if name not in seen:
            speakers.append(name)
            seen.add(name)
        entries.append({
            "timestamp": entry.get("timestamp", "00:00:00"),
            "speaker": name,
            "text": entry.get("text", ""),
        })

    return {
        "title": title,
        "date": date_prefix,
        "speakers": speakers,
        "entries": entries,
    }


def format_transcript(parsed: dict) -> str:
    """Build human-readable transcript text from parsed data."""
    lines = [
        f"Meeting: {parsed['title']}",
        f"Date: {parsed['date']}",
        f"Attendees: {', '.join(parsed['speakers'])}",
        "=" * 80,
        "",
    ]
    for entry in parsed["entries"]:
        lines.append(f"[{entry['timestamp']}] {entry['speaker']}:")
        lines.append(entry["text"])
        lines.append("")
    return "\n".join(lines)


def sanitize_filename(name: str) -> str:
    """Convert a meeting title to a safe filename slug."""
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = name.strip("-")
    return name[:80] or "meeting"


def save_transcript(content: str, date_prefix: str, title: str) -> Path:
    """Save transcript text to workspace. Returns the file path."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    safe_title = sanitize_filename(title)
    filename = f"{date_prefix}_{safe_title}.txt"
    filepath = WORKSPACE / filename
    filepath.write_text(content)
    return filepath


def list_transcripts(count: int = 0) -> list[dict]:
    """Scan workspace for transcripts, return sorted by date desc.

    Each entry: {filename, title, date, attendees}.
    """
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    results = []
    for path in WORKSPACE.glob("*.txt"):
        info = _parse_header(path)
        if info:
            results.append(info)
    results.sort(key=lambda x: x["date"], reverse=True)
    if count > 0:
        results = results[:count]
    return results


def read_transcript(filename: str) -> str:
    """Read full transcript content. Raises FileNotFoundError if missing."""
    filepath = WORKSPACE / filename
    if not filepath.exists():
        raise FileNotFoundError(f"Transcript not found: {filename}")
    return filepath.read_text()


def search_transcripts(query: str) -> list[dict]:
    """Case-insensitive search across all transcripts.

    Returns matches with filename, title, date, and matching context lines.
    """
    query_lower = query.lower()
    results = []
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    for path in WORKSPACE.glob("*.txt"):
        content = path.read_text()
        if query_lower not in content.lower():
            continue
        info = _parse_header(path) or {"filename": path.name, "title": "", "date": "", "attendees": ""}
        # Collect context lines
        context_lines = []
        for line in content.splitlines():
            if query_lower in line.lower():
                context_lines.append(line.strip())
        info["matches"] = context_lines[:10]  # Cap at 10 matches per file
        results.append(info)
    results.sort(key=lambda x: x["date"], reverse=True)
    return results


def _parse_header(path: Path) -> dict | None:
    """Parse Meeting/Date/Attendees from the first lines of a transcript file."""
    try:
        text = path.read_text()
    except OSError:
        return None
    title = ""
    date = ""
    attendees = ""
    for line in text.splitlines()[:5]:
        if line.startswith("Meeting: "):
            title = line[9:]
        elif line.startswith("Date: "):
            date = line[6:]
        elif line.startswith("Attendees: "):
            attendees = line[11:]
    return {
        "filename": path.name,
        "title": title,
        "date": date,
        "attendees": attendees,
    }
