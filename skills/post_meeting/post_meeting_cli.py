#!/usr/bin/env python3
"""CLI for the post_meeting sub-agent. Thin wrapper over agent.knowledge.models."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Make the project importable when invoked via `cd skills/post_meeting`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.knowledge import db as kdb  # noqa: E402
from agent.knowledge import models  # noqa: E402


def _print(payload) -> None:
    print(json.dumps(payload, default=str))


def _parse_participants(s: str | None) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def cmd_match_person(args):
    p = models.match_person(name=args.name, email=args.email)
    _print(p or {"id": None})


def cmd_create_interaction(args):
    i = models.create_interaction(
        interaction_id=args.id,
        type=args.type,
        title=args.title,
        occurred_at=args.occurred_at,
        content_path=args.content_path,
        source=args.source,
        source_ref=args.source_ref,
        participant_ids=_parse_participants(args.participants),
    )
    _print(i)


def cmd_update_summary(args):
    conn = kdb.connect()
    try:
        conn.execute(
            "UPDATE interactions SET summary = ? WHERE id = ?",
            (args.summary, args.id),
        )
        conn.commit()
        r = conn.execute("SELECT * FROM interactions WHERE id = ?", (args.id,)).fetchone()
        _print(dict(r) if r else {"error": "not found"})
    finally:
        conn.close()


def cmd_propose_task(args):
    payload = {
        "title": args.title,
        "description": args.description,
        "due_at": args.due_at,
        "person_id": args.person_id,
    }
    item = models.create_inbox_item(
        type="task_proposal",
        payload=payload,
        source_interaction_id=args.interaction_id,
    )
    _print(item)


def cmd_propose_contact_update(args):
    try:
        patch = json.loads(args.patch)
    except json.JSONDecodeError as e:
        _print({"error": f"bad --patch json: {e}"})
        sys.exit(2)
    payload = {"person_id": args.person_id, "patch": patch}
    item = models.create_inbox_item(
        type="contact_update",
        payload=payload,
        source_interaction_id=args.interaction_id,
    )
    _print(item)


def cmd_propose_new_person(args):
    try:
        fields = json.loads(args.fields)
    except json.JSONDecodeError as e:
        _print({"error": f"bad --fields json: {e}"})
        sys.exit(2)
    item = models.create_inbox_item(
        type="new_person",
        payload={"fields": fields},
        source_interaction_id=args.interaction_id,
    )
    _print(item)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("match-person")
    p.add_argument("--name")
    p.add_argument("--email")
    p.set_defaults(fn=cmd_match_person)

    p = sub.add_parser("create-interaction")
    p.add_argument("--id", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--title")
    p.add_argument("--occurred-at")
    p.add_argument("--content-path")
    p.add_argument("--source")
    p.add_argument("--source-ref")
    p.add_argument("--participants", default="")
    p.set_defaults(fn=cmd_create_interaction)

    p = sub.add_parser("update-interaction-summary")
    p.add_argument("--id", required=True)
    p.add_argument("--summary", required=True)
    p.set_defaults(fn=cmd_update_summary)

    p = sub.add_parser("propose-task")
    p.add_argument("--interaction-id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description")
    p.add_argument("--due-at")
    p.add_argument("--person-id")
    p.set_defaults(fn=cmd_propose_task)

    p = sub.add_parser("propose-contact-update")
    p.add_argument("--interaction-id", required=True)
    p.add_argument("--person-id", required=True)
    p.add_argument("--patch", required=True, help="JSON dict")
    p.set_defaults(fn=cmd_propose_contact_update)

    p = sub.add_parser("propose-new-person")
    p.add_argument("--interaction-id", required=True)
    p.add_argument("--fields", required=True, help="JSON dict")
    p.set_defaults(fn=cmd_propose_new_person)

    args = ap.parse_args()
    try:
        args.fn(args)
    except sqlite3.Error as e:
        _print({"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
