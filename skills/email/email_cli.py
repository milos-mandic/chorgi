#!/usr/bin/env python3
"""CLI wrapper for email operations. Used by the email sub-agent via Bash."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add skill directory to path for local import
sys.path.insert(0, str(Path(__file__).parent))
import email_client

DRAFTS_DIR = Path(__file__).parent / "workspace" / "drafts"


def cmd_check(args):
    """Check unread emails."""
    emails = email_client.fetch_unread(count=args.count)
    if not emails:
        print("No unread emails.")
    else:
        print(json.dumps(emails, indent=2))


def cmd_read(args):
    """Read a specific email by UID."""
    result = email_client.read_email(args.uid)
    print(json.dumps(result, indent=2))


def cmd_search(args):
    """Search emails by query."""
    results = email_client.search_emails(args.query, max_results=args.max)
    if not results:
        print(f"No emails matching '{args.query}'.")
    else:
        print(json.dumps(results, indent=2))


def cmd_send(args):
    """Send an email."""
    result = email_client.send_email(args.to, args.subject, args.body)
    print(result)


def cmd_draft(args):
    """Save a draft email for review."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    draft = {
        "to": args.to,
        "subject": args.subject,
        "body": args.body,
        "created": datetime.now().isoformat(),
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"draft_{timestamp}.json"
    draft_path = DRAFTS_DIR / filename
    draft_path.write_text(json.dumps(draft, indent=2))
    print(f"Draft saved: {draft_path}")
    print(json.dumps(draft, indent=2))


def cmd_list_drafts(args):
    """List saved drafts."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    drafts = sorted(DRAFTS_DIR.glob("draft_*.json"))
    if not drafts:
        print("No drafts found.")
        return
    for d in drafts:
        try:
            data = json.loads(d.read_text())
            print(f"  {d.name}: To={data['to']} Subject={data['subject']}")
        except (json.JSONDecodeError, KeyError):
            print(f"  {d.name}: (invalid)")


def cmd_send_draft(args):
    """Send a saved draft."""
    draft_path = Path(args.draft_file)
    if not draft_path.exists():
        # Try looking in drafts dir
        draft_path = DRAFTS_DIR / args.draft_file
    if not draft_path.exists():
        print(f"Draft not found: {args.draft_file}")
        sys.exit(1)

    data = json.loads(draft_path.read_text())
    result = email_client.send_email(data["to"], data["subject"], data["body"])
    print(result)
    # Remove sent draft
    draft_path.unlink()
    print(f"Draft {draft_path.name} removed after sending.")


def cmd_folders(args):
    """List mailbox folders."""
    folders = email_client.list_folders()
    for f in folders:
        print(f"  {f}")


def main():
    parser = argparse.ArgumentParser(description="Email CLI for chorgi")
    sub = parser.add_subparsers(dest="command", required=True)

    # check
    p = sub.add_parser("check", help="Check unread emails")
    p.add_argument("--count", type=int, default=10)
    p.set_defaults(func=cmd_check)

    # read
    p = sub.add_parser("read", help="Read email by UID")
    p.add_argument("uid")
    p.set_defaults(func=cmd_read)

    # search
    p = sub.add_parser("search", help="Search emails")
    p.add_argument("query")
    p.add_argument("--max", type=int, default=10)
    p.set_defaults(func=cmd_search)

    # send
    p = sub.add_parser("send", help="Send an email")
    p.add_argument("to")
    p.add_argument("subject")
    p.add_argument("body")
    p.set_defaults(func=cmd_send)

    # draft
    p = sub.add_parser("draft", help="Draft an email")
    p.add_argument("to")
    p.add_argument("subject")
    p.add_argument("body")
    p.set_defaults(func=cmd_draft)

    # list-drafts
    p = sub.add_parser("list-drafts", help="List saved drafts")
    p.set_defaults(func=cmd_list_drafts)

    # send-draft
    p = sub.add_parser("send-draft", help="Send a saved draft")
    p.add_argument("draft_file")
    p.set_defaults(func=cmd_send_draft)

    # folders
    p = sub.add_parser("folders", help="List mailbox folders")
    p.set_defaults(func=cmd_folders)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
