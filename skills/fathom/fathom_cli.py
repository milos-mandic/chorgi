#!/usr/bin/env python3
"""CLI wrapper for Fathom transcript operations. Used by the fathom sub-agent via Bash."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fathom_client


def cmd_list(args):
    """List saved transcripts."""
    transcripts = fathom_client.list_transcripts(count=args.count)
    if not transcripts:
        print("No transcripts found.")
        return
    print(json.dumps(transcripts, indent=2))


def cmd_read(args):
    """Read a specific transcript."""
    try:
        content = fathom_client.read_transcript(args.filename)
        print(content)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        print("Use 'python fathom_cli.py list' to see available transcripts.")
        sys.exit(1)


def cmd_search(args):
    """Search across transcripts."""
    results = fathom_client.search_transcripts(args.query)
    if not results:
        print(f"No transcripts matching '{args.query}'.")
        return
    print(json.dumps(results, indent=2))


def cmd_latest(args):
    """Read the most recent transcript."""
    transcripts = fathom_client.list_transcripts(count=1)
    if not transcripts:
        print("No transcripts found.")
        print("Transcripts are saved automatically when Fathom webhooks arrive.")
        return
    content = fathom_client.read_transcript(transcripts[0]["filename"])
    print(content)


def main():
    parser = argparse.ArgumentParser(description="Fathom transcript CLI for chorgi")
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p = sub.add_parser("list", help="List saved transcripts")
    p.add_argument("--count", type=int, default=0, help="Limit results (0 = all)")
    p.set_defaults(func=cmd_list)

    # read
    p = sub.add_parser("read", help="Read a specific transcript")
    p.add_argument("filename", help="Transcript filename (from list output)")
    p.set_defaults(func=cmd_read)

    # search
    p = sub.add_parser("search", help="Search across transcripts")
    p.add_argument("query", help="Search term")
    p.set_defaults(func=cmd_search)

    # latest
    p = sub.add_parser("latest", help="Read the most recent transcript")
    p.set_defaults(func=cmd_latest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
