# Email Skill

Manages email via `chorgibot@gmail.com` using stdlib IMAP/SMTP. Supports reading, sending, searching, and a draft-review-send workflow.

## How it works

1. User asks to read, send, or search email
2. Haiku routes the message to this skill
3. Sub-agent uses `email_cli.py` to perform the operation
4. New emails are also checked every heartbeat (5 min) with Telegram notifications

---

## Setup

### 1. Gmail App Password

1. Enable 2-Step Verification on the Gmail account
2. Go to [App Passwords](https://myaccount.google.com/apppasswords)
3. Generate a new app password for "Mail"
4. Copy the 16-character password

### 2. Environment variables

Add to `.personal/secrets.env`:

```
GMAIL_ADDRESS=chorgibot@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

### 3. Restart the agent

```bash
python agent/main.py
```

---

## Features

- **Inbox check** — read unread emails
- **Search** — find emails by subject or sender
- **Send** — send plain text emails
- **Draft workflow** — drafts are saved as JSON in `workspace/drafts/`, reviewed by user, then sent via `send-draft`
- **Email polling** — scheduler checks for new emails every heartbeat and sends Telegram notifications (uses UID tracking to avoid duplicates)
