"""Markdown content file conventions + atomic write helpers."""

import os
import re
from pathlib import Path

from .db import PERSONAL_DIR

CONTENT_DIR = PERSONAL_DIR / "content"
PEOPLE_DIR = CONTENT_DIR / "people"
MEETINGS_DIR = CONTENT_DIR / "meetings"


def _ensure_dirs() -> None:
    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    MEETINGS_DIR.mkdir(parents=True, exist_ok=True)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "unknown"


def person_path(slug: str) -> Path:
    return PEOPLE_DIR / f"{slug}.md"


def meeting_path(interaction_id: str) -> Path:
    return MEETINGS_DIR / f"{interaction_id}.md"


def write_atomic(path: Path, content: str) -> None:
    _ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def append(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""
