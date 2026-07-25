"""Nightly backup of durable state — stdlib only.

Backs up knowledge.db (via the sqlite3 backup API, safe against concurrent
writers in WAL mode), schedules/, memory files, local chat conversations, and
skill workspace JSONs into dated directories under .personal/backups/,
pruning to the most recent KEEP_DAYS.
"""

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
KEEP_DAYS = 7


def run_backup(base_dir: Path = BASE_DIR, now: datetime | None = None) -> str:
    """Snapshot durable state into .personal/backups/YYYY-MM-DD/. Returns a summary."""
    now = now or datetime.now(timezone.utc)
    personal = base_dir / ".personal"
    dest = personal / "backups" / now.strftime("%Y-%m-%d")
    dest.mkdir(parents=True, exist_ok=True)

    copied = []

    db_path = personal / "knowledge.db"
    if db_path.exists():
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(dest / "knowledge.db"))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        copied.append("knowledge.db")

    for name, src_dir in (
        ("schedules", base_dir / "schedules"),
        ("memory", personal / "memory"),
        ("local_chat", personal / "local_chat"),
    ):
        if src_dir.is_dir():
            shutil.copytree(
                src_dir, dest / name, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("*.tmp", "*.lock"),
            )
            copied.append(name)

    workspace_count = 0
    for ws_json in sorted((base_dir / "skills").glob("*/workspace/*.json")):
        skill = ws_json.parent.parent.name
        target = dest / "workspaces" / skill
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ws_json, target / ws_json.name)
        workspace_count += 1
    if workspace_count:
        copied.append(f"{workspace_count} workspace JSONs")

    pruned = _prune_old(personal / "backups")

    summary = f"Backup {dest.name}: {', '.join(copied) or 'nothing to back up'}"
    if pruned:
        summary += f" (pruned {pruned} old)"
    logger.info(summary)
    return summary


def _prune_old(backup_root: Path) -> int:
    """Keep only the newest KEEP_DAYS dated backup dirs."""
    dated = sorted(
        d for d in backup_root.iterdir()
        if d.is_dir() and len(d.name) == 10 and d.name[4] == "-"
    )
    stale = dated[:-KEEP_DAYS] if len(dated) > KEEP_DAYS else []
    for d in stale:
        shutil.rmtree(d, ignore_errors=True)
    return len(stale)
