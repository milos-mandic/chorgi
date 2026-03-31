"""Parse forwarded emails from Milos to extract context and original content."""

import re

MILOS_EMAIL = "milos.mandic.etf@gmail.com"

# Gmail's forwarded message delimiter
_FWD_DELIMITER = "---------- Forwarded message ---------"


def is_forwarded_from_milos(email_dict: dict) -> bool:
    """Check if an email is a forward from Milos's personal account."""
    sender = email_dict.get("from", "")
    if MILOS_EMAIL not in sender.lower():
        return False

    subject = email_dict.get("subject", "")
    body = email_dict.get("body_preview", "") or email_dict.get("body", "")

    # Check for forward indicators
    subj_lower = subject.lower()
    if subj_lower.startswith("fwd:") or subj_lower.startswith("fw:"):
        return True
    if _FWD_DELIMITER in body:
        return True

    return False


def parse_forwarded_email(full_body: str) -> dict | None:
    """Parse a forwarded email into instructions and original content.

    Returns dict with keys:
        instructions, original_from, original_from_name,
        original_subject, original_date, original_body
    Or None if parsing fails.
    """
    if _FWD_DELIMITER not in full_body:
        return None

    parts = full_body.split(_FWD_DELIMITER, 1)
    instructions = parts[0].strip()
    remainder = parts[1].strip()

    # Parse headers from the forwarded section
    lines = remainder.split("\n")
    headers = {}
    body_start = 0

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            # First blank line after headers = start of body
            if headers:
                body_start = i + 1
                break
            continue

        # Match header lines: "From: ...", "Date: ...", etc.
        match = re.match(r"^(From|Date|Subject|To|Cc):\s*(.+)$", line_stripped, re.IGNORECASE)
        if match:
            headers[match.group(1).lower()] = match.group(2).strip()
        elif not headers:
            # Haven't found headers yet, skip
            continue
        else:
            # Non-header line after headers started = body begins here
            body_start = i
            break

    if not headers:
        return None

    original_from = headers.get("from", "")
    original_body = "\n".join(lines[body_start:]).strip()

    # Extract name from "Name <email>" format
    name_match = re.match(r"^(.+?)\s*<", original_from)
    original_from_name = name_match.group(1).strip().strip('"') if name_match else original_from

    return {
        "instructions": instructions,
        "original_from": original_from,
        "original_from_name": original_from_name,
        "original_subject": headers.get("subject", "(no subject)"),
        "original_date": headers.get("date", ""),
        "original_body": original_body,
    }
