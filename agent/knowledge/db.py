"""SQLite connection + migration runner."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PERSONAL_DIR = BASE_DIR / ".personal"
DB_PATH = PERSONAL_DIR / "knowledge.db"
SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


def connect() -> sqlite3.Connection:
    PERSONAL_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def run_migrations() -> list[str]:
    """Apply any pending schema/*.sql files in lex order. Returns names applied."""
    conn = connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row["name"] for row in conn.execute("SELECT name FROM _migrations")}
        files = sorted(SCHEMA_DIR.glob("*.sql"))
        newly = []
        for f in files:
            if f.name in applied:
                continue
            sql = f.read_text()
            try:
                conn.executescript("BEGIN; " + sql + "; COMMIT;")
            except sqlite3.Error as e:
                conn.rollback()
                logger.error("Migration %s failed: %s", f.name, e)
                raise
            conn.execute(
                "INSERT INTO _migrations(name, applied_at) VALUES (?, datetime('now'))",
                (f.name,),
            )
            conn.commit()
            newly.append(f.name)
            logger.info("Applied migration %s", f.name)
        return newly
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--migrate":
        applied = run_migrations()
        if applied:
            print("Applied:", ", ".join(applied))
        else:
            print("No pending migrations.")
    else:
        print("Usage: python -m agent.knowledge.db --migrate")
        sys.exit(1)
