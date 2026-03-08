"""Shared IMAP/SMTP email client using stdlib only."""

import email
import email.header
import email.utils
import imaplib
import json
import logging
import os
import re
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger(__name__)

SEEN_FILE = Path(__file__).parent / "workspace" / "email_seen.json"


def _get_credentials() -> tuple[str, str]:
    """Return (address, app_password) from env vars."""
    addr = os.environ.get("GMAIL_ADDRESS", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not addr or not pw:
        raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set")
    return addr, pw


def _connect_imap() -> imaplib.IMAP4_SSL:
    addr, pw = _get_credentials()
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    conn.login(addr, pw)
    return conn


def _connect_smtp() -> smtplib.SMTP:
    addr, pw = _get_credentials()
    conn = smtplib.SMTP("smtp.gmail.com", 587)
    conn.ehlo()
    conn.starttls()
    conn.ehlo()
    conn.login(addr, pw)
    return conn


def _decode_header(value: str) -> str:
    """Decode RFC 2047 encoded header values."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _extract_body(msg: email.message.Message, max_chars: int = 2000) -> str:
    """Extract text body from email message, handling multipart."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    return text[:max_chars]
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    text = re.sub(r"<[^>]+>", " ", text)
                    text = re.sub(r"\s+", " ", text).strip()
                    return text[:max_chars]
        return "(no text content)"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
            return text[:max_chars]
        return "(empty)"


def _msg_to_dict(msg: email.message.Message, uid: str, preview: bool = True) -> dict:
    """Convert email message to dict."""
    body = _extract_body(msg, max_chars=200 if preview else 4000)
    return {
        "uid": uid,
        "from": _decode_header(msg.get("From", "")),
        "subject": _decode_header(msg.get("Subject", "(no subject)")),
        "date": msg.get("Date", ""),
        "body_preview" if preview else "body": body,
    }


def fetch_unread(count: int = 10) -> list[dict]:
    """Fetch unread emails from inbox."""
    conn = _connect_imap()
    try:
        conn.select("INBOX")
        _, data = conn.search(None, "UNSEEN")
        uids = data[0].split()
        if not uids:
            return []
        uids = uids[-count:]
        results = []
        for uid in reversed(uids):
            _, msg_data = conn.fetch(uid, "(BODY.PEEK[])")
            if msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            results.append(_msg_to_dict(msg, uid.decode()))
        return results
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass


def fetch_recent(count: int = 10) -> list[dict]:
    """Fetch most recent emails regardless of read status."""
    conn = _connect_imap()
    try:
        conn.select("INBOX")
        _, data = conn.search(None, "ALL")
        uids = data[0].split()
        if not uids:
            return []
        uids = uids[-count:]
        results = []
        for uid in reversed(uids):
            _, msg_data = conn.fetch(uid, "(BODY.PEEK[])")
            if msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            results.append(_msg_to_dict(msg, uid.decode()))
        return results
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass


def search_emails(query: str, max_results: int = 10) -> list[dict]:
    """Search emails using IMAP search criteria."""
    conn = _connect_imap()
    try:
        conn.select("INBOX")
        criteria = f'(OR SUBJECT "{query}" FROM "{query}")'
        _, data = conn.search(None, criteria)
        uids = data[0].split()
        if not uids:
            return []
        uids = uids[-max_results:]
        results = []
        for uid in reversed(uids):
            _, msg_data = conn.fetch(uid, "(BODY.PEEK[])")
            if msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            results.append(_msg_to_dict(msg, uid.decode()))
        return results
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass


def read_email(uid: str) -> dict:
    """Read a full email by UID."""
    conn = _connect_imap()
    try:
        conn.select("INBOX")
        _, msg_data = conn.fetch(uid.encode(), "(RFC822)")
        if msg_data[0] is None:
            return {"error": f"Email UID {uid} not found"}
        msg = email.message_from_bytes(msg_data[0][1])
        return _msg_to_dict(msg, uid, preview=False)
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email via SMTP."""
    addr, _ = _get_credentials()
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = addr
    msg["To"] = to

    conn = _connect_smtp()
    try:
        conn.sendmail(addr, [to], msg.as_string())
        return f"Email sent to {to}"
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def send_html_email(to: str, subject: str, html_body: str) -> str:
    """Send an HTML email via SMTP."""
    addr, _ = _get_credentials()
    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = addr
    msg["To"] = to

    conn = _connect_smtp()
    try:
        conn.sendmail(addr, [to], msg.as_string())
        return f"Email sent to {to}"
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def list_folders() -> list[str]:
    """List available IMAP folders."""
    conn = _connect_imap()
    try:
        _, folders = conn.list()
        result = []
        for f in folders:
            name = f.decode().split('"/"')[-1].strip().strip('"')
            result.append(name)
        return result
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ── Seen UID tracking for monitor ──────────────────────────────

def _load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(list(seen)))
    tmp.replace(SEEN_FILE)


def check_new_emails() -> list[dict]:
    """Fetch unread emails that haven't been seen before (for monitoring)."""
    seen = _load_seen()
    unread = fetch_unread(count=20)
    new = [e for e in unread if e["uid"] not in seen]
    if new:
        seen.update(e["uid"] for e in new)
        _save_seen(seen)
    return new
