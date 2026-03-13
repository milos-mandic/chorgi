# Calendar Skill

Manages Google Calendar events and provides smart scheduling with conflict protection.

## How it works

1. User asks to check schedule, create events, or find free time
2. Haiku routes the message to this skill
3. Sub-agent uses `calendar_cli.py` to interact with Google Calendar API
4. Two calendars: read-only access to user's private calendar (availability), full access to bot calendar (event creation)

---

## Setup

### 1. GCP OAuth credentials

1. Create a project in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the Google Calendar API
3. Create an OAuth 2.0 Client ID (Desktop app type)
4. Download the credentials JSON
5. Save as `skills/calendar/oauth_credentials.json`

### 2. Python dependencies

```bash
pip install google-auth google-auth-oauthlib google-api-python-client
```

### 3. Environment variables

Add to `.personal/secrets.env`:

```
CALENDAR_OWNER_ID=your_email@gmail.com
CALENDAR_BOT_ID=chorgibot@gmail.com
```

### 4. First-run OAuth

On first use, a browser window will open for Google OAuth consent. The refresh token is stored in `skills/calendar/token.json` and reused automatically.

### 5. Restart the agent

```bash
python agent/main.py
```

---

## Features

- **List events** — view upcoming events from either calendar
- **Find free slots** — check availability across both calendars
- **Create events** — creates on bot calendar, auto-invites owner
- **Conflict protection** — refuses to schedule over busy time (override with `--force`)
- **Smart scheduling** — `suggest` command scores time slots based on task type, importance, deadlines, and user preferences (see `preferences.md`)
- **Update/delete** — modify or remove existing events
