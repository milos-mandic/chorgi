"""Seed CLI — import people from H2-per-person markdown template.

Format:
    ## Person Name
    role: FDE
    company: Anthropic
    linkedin: https://linkedin.com/in/...
    email: a@b.com
    x: @handle
    tags: fde, ai
    source: intro from X

    Free markdown notes here until the next ## heading.

Usage:
    python -m agent.knowledge.seed people < notes.md
    python -m agent.knowledge.seed people --file path/to/notes.md
    python -m agent.knowledge.seed people --dry-run < notes.md
"""

import argparse
import sys

from . import models

FIELD_KEYS = {
    "role": "role",
    "company": "company",
    "linkedin": "linkedin_url",
    "linkedin_url": "linkedin_url",
    "email": "email",
    "x": "x_handle",
    "x_handle": "x_handle",
    "twitter": "x_handle",
    "source": "source",
}


def parse_blocks(text: str) -> list[dict]:
    """Split on H2 headings, return list of {name, fields, tags, notes}."""
    blocks = []
    current: dict | None = None
    body_lines: list[str] = []
    in_kv = True

    def flush():
        if current is not None:
            notes = "\n".join(body_lines).strip()
            current["notes"] = notes or None
            blocks.append(current)

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            flush()
            current = {"name": line[3:].strip(), "fields": {}, "tags": []}
            body_lines = []
            in_kv = True
            continue
        if current is None:
            continue
        if in_kv:
            if not line.strip():
                in_kv = False
                continue
            if ":" in line and not line.startswith(" "):
                key, _, val = line.partition(":")
                k = key.strip().lower()
                v = val.strip()
                if k == "tags":
                    current["tags"] = [t.strip() for t in v.split(",") if t.strip()]
                elif k in FIELD_KEYS:
                    current["fields"][FIELD_KEYS[k]] = v
                else:
                    in_kv = False
                    body_lines.append(line)
                continue
            in_kv = False
            body_lines.append(line)
        else:
            body_lines.append(line)
    flush()
    return blocks


def import_people(blocks: list[dict], dry_run: bool = False) -> int:
    n = 0
    for b in blocks:
        if not b.get("name"):
            continue
        if dry_run:
            print(f"[dry] {b['name']} fields={b['fields']} tags={b['tags']} "
                  f"notes={'yes' if b.get('notes') else 'no'}")
        else:
            p = models.upsert_person(
                name=b["name"],
                tags=b.get("tags") or None,
                notes=b.get("notes"),
                **b["fields"],
            )
            print(f"+ {p['name']} ({p['slug']}) -> {p['id']}")
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="Seed the knowledge DB.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_people = sub.add_parser("people", help="Import people from markdown.")
    p_people.add_argument("--file", help="Read from file instead of stdin.")
    p_people.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.cmd == "people":
        text = open(args.file).read() if args.file else sys.stdin.read()
        blocks = parse_blocks(text)
        n = import_people(blocks, dry_run=args.dry_run)
        print(f"\n{n} people {'parsed' if args.dry_run else 'imported'}.")


if __name__ == "__main__":
    main()
