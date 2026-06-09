"""Knowledge layer — SQLite-backed structured state + markdown content files.

DB lives at .personal/knowledge.db; long-form content under .personal/content/.
Sub-agents read via the sqlite3 CLI and write via post_meeting_cli (which
calls into models.py).
"""
