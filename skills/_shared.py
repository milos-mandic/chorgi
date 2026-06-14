"""Shared JSON persistence helpers for skill CLIs and the agent process.

Skill CLIs run as separate processes (sometimes concurrently as parallel
sub-agents) and the bot process mutates the same JSON files from its webhook
thread. Two guarantees here:

- save_json is atomic (tmp + os.replace), so a crash mid-write can never
  leave a truncated file behind.
- file_lock gives cross-process mutual exclusion via flock on a sidecar
  .lock file, so concurrent read-modify-write cycles can't lose writes.

Import pattern from a skill CLI (they run as plain scripts):

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import _shared
"""

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Single source of truth for the user's local timezone. Both skill CLIs
# (task_cli.py, calendar_cli.py) and the agent process import this so the
# zone is defined exactly once instead of hardcoded in several places.
LOCAL_TZ = ZoneInfo("Europe/Berlin")


def now_local() -> datetime:
    """Current time as a timezone-aware datetime in LOCAL_TZ."""
    return datetime.now(LOCAL_TZ)


def now_context_line() -> str:
    """A single line stating the current date/time, for LLM context.

    Injected at the top of router and sub-agent context so relative dates
    ("tomorrow", "next Friday", "3pm") resolve against a real anchor.
    """
    local = now_local()
    return (
        f"Current date/time: {local.strftime('%A %Y-%m-%d %H:%M')} "
        f"({local.tzname()}, {local.isoformat()})"
    )


def load_json(path, default=None, *, tolerant=False):
    """Load JSON from path. Returns `default` if the file doesn't exist.

    Corrupt JSON raises by default (a mutation that silently starts from
    `default` would overwrite good data on save); pass tolerant=True for
    read-only paths that prefer an empty result over an exception.
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        if tolerant:
            return default
        raise


def save_json(path, data):
    """Write JSON atomically: a reader never sees a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


@contextmanager
def file_lock(path):
    """Exclusive cross-process lock on a sidecar <path>.lock file.

    Hold this across a whole load → modify → save cycle. Do not nest
    on the same path within one thread — flock on a fresh fd would
    self-deadlock.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


@contextmanager
def locked_json(path, default=None):
    """Read-modify-write a JSON file under file_lock.

    Yields the loaded data (or `default`); writes it back atomically on
    clean exit. Mutate the yielded object in place — rebinding the name
    inside the `with` block does not change what gets saved.
    """
    with file_lock(path):
        data = load_json(path, default)
        yield data
        save_json(path, data)
