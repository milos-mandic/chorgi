# Agent Harness

Personal assistant agent harness. Talks to you via Telegram, responds instantly via Haiku, and spawns Claude Code sub-agents for deep work. All four implementation phases are complete.

## Design Principles

1. **Harness is generic, config is personal.** No tokens, no personal context in the repo. Everything personal lives in `.personal/` (gitignored).
2. **Skills are plug-and-play.** A skill = a folder with `CLAUDE.md` + `config.json`. Drop it in `/skills/`, it's auto-discovered on next heartbeat.
3. **Haiku for speed, Claude Code for depth.** Haiku handles routing + instant responses. Claude Code CLI handles tool use and multi-step work.
4. **Native Claude Code conventions.** Each skill is a self-contained Claude Code project — testable standalone with `cd skills/<name> && claude`.
5. **Stdlib-first Python.** Only external dependency is `python-telegram-bot`. API calls use `urllib.request` directly (no `anthropic` SDK).

---

## Repository Structure

```
chorgi_v1/
├── CLAUDE.md                     # This file
├── requirements.txt              # python-telegram-bot>=21.0,<22.0
├── setup.py                      # CLI onboarding — creates .personal/ from templates
│
├── agent/                        # The harness
│   ├── __init__.py
│   ├── main.py                   # Entry point — Telegram bot + scheduler + webhook + onboarding handlers
│   ├── voice.py                  # Voice support — Whisper STT + OpenAI TTS (stdlib-only)
│   ├── webhook.py                # Webhook server — threaded HTTP, Fathom handler, signature verification
│   ├── orchestrator.py           # Message routing (3 routes), sub-agent lifecycle, schedule saving, webhook dispatch
│   ├── haiku.py                  # Haiku classify+respond (JSON parsing with fallbacks)
│   ├── api_client.py             # Stdlib-only Anthropic API client (urllib, asyncio.to_thread)
│   ├── spawner.py                # Claude Code sub-agent launcher (asyncio.create_subprocess_exec)
│   ├── scheduler.py              # Heartbeat loop (5 min) + scheduled task execution
│   ├── memory.py                 # Context assembly, short-term pruning, Haiku-driven promotion
│   ├── onboarding.py             # Telegram ConversationHandler for profile setup
│   └── skill_registry.py         # Auto-discovery, fingerprinting, router prompt generation
│
├── skills/
│   ├── general/                  # Ships with repo — the starter skill
│   │   ├── CLAUDE.md             # Skill behavior definition
│   │   ├── config.json           # Router metadata: name, description, tools, max_turns, timeout
│   │   └── workspace/            # Sub-agent scratch directory
│   ├── fathom/                    # Fathom meeting transcript processor
│   │   ├── CLAUDE.md             # Summarization instructions (2-4 bullets)
│   │   ├── config.json           # tools: Read,Bash,Write — timeout: 60s
│   │   └── workspace/            # Saved transcripts (YYYY-MM-DD_title.txt)
│   ├── email/                    # Email via Gmail IMAP/SMTP
│   │   ├── CLAUDE.md             # CLI command reference
│   │   ├── config.json           # tools: Bash,Read,Write — timeout: 90s
│   │   ├── email_client.py       # Stdlib-only IMAP/SMTP client
│   │   ├── email_cli.py          # CLI wrapper for sub-agent use
│   │   └── workspace/
│   │       └── drafts/           # Saved email drafts (JSON)
│   └── calendar/                 # Google Calendar management
│       ├── CLAUDE.md             # CLI command reference
│       ├── config.json           # tools: Bash,Read,Write — timeout: 90s
│       ├── calendar_client.py    # Google Calendar API wrapper (service account auth)
│       ├── calendar_cli.py       # CLI wrapper for sub-agent use
│       ├── scheduler.py          # Smart scheduling engine (slot scoring)
│       ├── preferences.md        # User scheduling preferences
│       └── workspace/
│
├── templates/                    # Templates for .personal/ files
│   ├── identity.md.template      # Placeholders: {name}, {role}, {style}
│   ├── context.md.template       # Placeholders: {projects}
│   └── secrets.env.template
│
├── schedules/                    # Schedule JSON files (gitignored except templates)
│
├── launchd/
│   └── com.chorgi.bot.plist      # launchd Launch Agent definition (installed by setup_launchd.sh)
│
├── bin/
│   ├── bot                       # Management helper: start/stop/restart/status/logs/tail
│   └── setup_launchd.sh          # One-time install: bot launchd agent + cloudflared system daemon
│
├── watchdog.sh                   # Minimal exec wrapper (launchd preferred for production)
│
└── .personal/                    # User config (gitignored, created by setup.py or /setup)
    ├── identity.md
    ├── context.md
    ├── secrets.env               # ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, OPENAI_API_KEY, WEBHOOK_SECRET, WEBHOOK_PORT, FATHOM_WEBHOOK_SECRET, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GOOGLE_OAUTH_CREDENTIALS, CALENDAR_OWNER_ID, CALENDAR_BOT_ID
    ├── costs.log                 # Append-only cost tracking
    └── memory/
        ├── long_term.md
        ├── short_term.md
        └── scratch.md
```

---

## Module Reference

### `agent/api_client.py`
Stdlib-only HTTP client for Anthropic Messages API. No SDK dependency.
- `_call_messages_sync(system, messages, max_tokens, model)` — blocking `urllib.request` call
- `call_haiku(system, messages, max_tokens)` — async wrapper via `asyncio.to_thread`, hardcoded to `claude-haiku-4-5-20251001`

### `agent/haiku.py`
Single Haiku call that classifies intent and optionally responds inline.
- `classify_and_respond(message, history, context, router_prompt)` — returns parsed JSON dict with `_usage` key injected
- Robust JSON parsing: tries raw → markdown fenced → first `{...}` → fallback to haiku response

### `agent/skill_registry.py`
- `discover_skills()` — scans `skills/*/` for dirs containing both `config.json` and `CLAUDE.md`, returns `{name: config_dict}`
- `get_skills_fingerprint(skills)` — returns `{name: config.json mtime}` for change detection
- `build_router_prompt(skills)` — generates the Haiku system prompt with three routes: `haiku`, `sub_agent`, `schedule`

### `agent/spawner.py`
- `spawn_sub_agent(skill_config, task, context)` — runs `claude --print --output-format json --allowedTools ... --max-turns ... --cwd <skill_dir>` via `asyncio.create_subprocess_exec`
- Passes dynamic context + task via stdin; Claude Code reads skill's `CLAUDE.md` automatically from `--cwd`
- Returns `{"text": ..., "elapsed_s": ...}` on success, `{"error": True, "message": ..., "elapsed_s": ...}` on failure
- Handles timeout (kills process), non-JSON output (returns raw text), and various JSON output shapes
- Strips `CLAUDECODE` env var to prevent "nested session" errors and `ANTHROPIC_API_KEY` to force OAuth

### `agent/memory.py`
- `Memory(personal_dir)` — manages all `.personal/` file I/O
- `get_haiku_context()` — identity + context + last 20 lines of short_term (compressed)
- `get_full_context()` — identity + context + full long_term + full short_term
- `append_short_term(entry)` — timestamped append
- `prune_short_term()` — keeps last 100 lines
- `promote_to_long_term(haiku_fn)` — sends short_term to Haiku, which returns JSON with `promote` and `remove_lines` lists
- `read_scratch()` / `clear_scratch()` — scratch pad for sub-agent initiated notifications

### `agent/webhook.py`
Threaded HTTP server for receiving external webhooks. Merged server + Fathom handler in one file.
- `WebhookServer` — creates `HTTPServer` on a daemon thread, configurable port (default 8443)
- `start(loop, orchestrator)` — stores event loop + orchestrator refs, starts server thread. No-op if `WEBHOOK_SECRET` not set.
- `stop()` — shutdown + join
- `_trigger_skill(skill, task)` — bridges HTTP thread → async orchestrator via `asyncio.run_coroutine_threadsafe()` (fire-and-forget)
- Path routing: `GET /<WEBHOOK_SECRET>/health` → 200, `POST /<WEBHOOK_SECRET>/fathom` → Fathom handler
- `_verify_fathom(headers, body)` — HMAC-SHA256 signature verification (Svix format: `webhook-id`, `webhook-timestamp`, `webhook-signature`), 5-minute replay protection, `whsec_` prefix support
- `_handle_fathom(headers, body, server)` — verifies signature, parses JSON, extracts speakers/transcript, formats as text, saves to `skills/fathom/workspace/<date>_<title>.txt`, triggers fathom skill
- Env vars: `WEBHOOK_SECRET` (URL path token), `WEBHOOK_PORT` (default 8443), `FATHOM_WEBHOOK_SECRET` (HMAC key)

### `agent/orchestrator.py`
Central coordinator. Key attributes and methods:
- `__init__(authorized_user_id)` — discovers skills, builds router prompt, stores fingerprint, creates Memory, initializes semaphore (max 2 concurrent)
- `classify(message, user_id)` — auth check → Haiku classify → routes to one of:
  - `"haiku"` → returns `{"type": "haiku", "response": ...}`
  - `"schedule"` → calls `_save_schedule()`, returns as haiku type
  - `"sub_agent"` → returns `{"type": "sub_agent", "skill": ..., "summary": ...}`
- `execute_sub_agent(classification)` — spawns Claude Code, logs cost, appends to short_term
- `handle_message(message, user_id)` — convenience: classify + execute in one call
- `_spawn_with_limit(...)` — semaphore-guarded spawn with queue notification
- `reload_skills()` — compares fingerprints, reloads on change, logs added/removed/updated
- `_save_schedule(schedule)` — sanitizes name, writes JSON to `schedules/`, returns `(ok, detail)`
- `haiku_query(prompt)` — standalone Haiku call for scheduler
- `run_scheduled_task(skill, task)` — spawn sub-agent for scheduled work
- `trigger_webhook_skill(skill, task)` — spawn sub-agent for webhook-triggered task, sends result to user via Telegram, logs to short_term
- `check_scratch_pad()` — reads scratch.md, notifies user if non-empty, clears
- `_log_cost(event_type, **kwargs)` — appends to `.personal/costs.log`
- `send_to_user` — callback set by `main.py` after bot init

### `agent/scheduler.py`
- `Scheduler(orchestrator)` — heartbeat loop every 300s
- `_heartbeat()` — prune short_term → promote to long_term → check scratch pad → reload skills → check emails
- `_check_emails()` — polls for unseen emails via `email_client.check_new_emails()`, sends Telegram notification for each new email (non-critical, wrapped in try/except)
- `_check_schedules()` — scans `schedules/*.json`, evaluates triggers, executes due tasks
- `_is_due(schedule, now)` — supports `daily` (at_hour, UTC) and `interval` (interval_minutes) triggers
- `_execute(schedule)` — dispatches to `haiku_query` or `run_scheduled_task`, optionally notifies user
- `_mark_ran(schedule_path, now)` — updates `last_run` ISO timestamp in the JSON file

### `agent/onboarding.py`
Telegram ConversationHandler for profile setup (4 fields: name, role, style, projects).
- States: `ASK_NAME=0`, `ASK_ROLE=1`, `ASK_STYLE=2`, `ASK_PROJECTS=3`, `CONFIRM=4`
- `start_onboarding(update, ctx)` → asks name, returns `ASK_NAME`
- `handle_name/role/style/projects` → stores answer, asks next question
- `handle_confirm` → on "yes" calls `write_profile()`, on anything else restarts
- `cancel` → handles `/cancel`
- `write_profile(data)` → reads templates, does `str.replace()` for placeholders, writes `identity.md` and `context.md`, ensures memory dir exists

### `agent/voice.py`
Voice support — OpenAI Whisper transcription + TTS via stdlib HTTP. No `openai` SDK.
- `transcribe_audio(audio_path, timeout=90)` — POST to Whisper API, returns transcript text
- `tts_generate(text, voice="alloy", timeout=30)` — POST to TTS API, returns path to .ogg file in /tmp
- Both raise `RuntimeError` if `OPENAI_API_KEY` is missing or API fails

### `agent/main.py`
Entry point. Loads secrets, creates Orchestrator, builds Telegram Application.
- `load_secrets()` — reads `.personal/secrets.env`, env vars override
- `_NeedsOnboardingFilter` — custom PTB filter, returns True if `identity.md` doesn't exist
- `setup_command` — `/setup` handler, auth check → `start_onboarding()`
- `auto_onboard` — auto-triggers onboarding on first message if no profile
- `handle_message` — classify → respond (haiku) or ack + execute (sub_agent)
- `handle_voice` — download .ogg → Whisper transcribe → classify → respond with voice (TTS) + text
- `_send_response` — splits messages >4096 chars for Telegram's limit
- `post_init` — wires `send_to_user` callback, starts scheduler as background task, starts webhook server
- Handler registration order: `ConversationHandler` (onboarding) first, then text `MessageHandler`, then voice `MessageHandler`

### `setup.py`
CLI onboarding script. Collects API keys + profile info interactively, creates `.personal/` directory with all files. Checks for `claude` CLI in PATH. Requires Python 3.11+.

---

## Message Routing

Three routes, classified by a single Haiku call:

| Route | When | Response time |
|-------|------|---------------|
| `haiku` | Greetings, chitchat, simple questions | ~1-2s |
| `schedule` | "Remind me every morning at 7 to..." | ~1-2s |
| `sub_agent` | Tool use, research, code, file ops, multi-step tasks | ~15-90s (ack in ~1s) |

The `schedule` route returns a complete schedule JSON from Haiku, which the orchestrator writes to `schedules/`. It resolves as a haiku-type response (no changes needed in main.py).

**Voice messages:** When a user sends a Telegram voice message, `handle_voice()` downloads the .ogg, transcribes it via OpenAI Whisper, routes the text through normal classification, then responds with both a voice message (OpenAI TTS) and text. Requires `OPENAI_API_KEY` (optional — voice features degrade gracefully without it).

---

## Skill System

A skill = a directory in `skills/` with:
- `CLAUDE.md` — static behavior definition (read automatically by Claude Code via `--cwd`)
- `config.json` — `{name, description, tools[], max_turns, timeout_seconds}`
- `workspace/` — scratch directory for sub-agent file operations

The `description` field drives routing — Haiku reads it to decide which skill to dispatch to.

**Hot-reload:** On each heartbeat, `reload_skills()` compares `config.json` mtimes. On change, it re-discovers all skills, rebuilds the router prompt, and logs what changed. No restart needed.

**Adding a skill:**
```bash
mkdir -p skills/research/workspace
# Write skills/research/CLAUDE.md and skills/research/config.json
# Wait for next heartbeat (or restart)
```

**Testing standalone:**
```bash
cd skills/general
echo "test task" | claude --print --allowedTools "Bash,Read,Write"
```

---

## Schedule System

JSON files in `schedules/`. Two trigger types:
- `daily` — fires once per day at `at_hour` (UTC)
- `interval` — fires every `interval_minutes` minutes

Schedules can be created two ways:
1. Drop a JSON file in `schedules/`
2. Tell the bot in natural language (e.g., "brief me every morning at 7") — Haiku extracts the schedule definition

Schedule JSON schema:
```json
{
  "name": "snake_case_slug",
  "trigger": "daily|interval",
  "at_hour": 7,
  "interval_minutes": 60,
  "type": "haiku|sub_agent",
  "skill": "general",
  "prompt": "what to do",
  "notify_user": true
}
```

The scheduler writes `last_run` (ISO timestamp) back to the file after execution.

---

## Onboarding

Two paths to set up a profile:

1. **CLI:** `python setup.py` — collects API keys + profile, creates `.personal/`
2. **Telegram:** `/setup` command or auto-triggered on first message if no `identity.md` exists
   - Collects 4 fields: name, role, communication style, current projects
   - Writes `identity.md` and `context.md` from templates
   - Cannot collect secrets (API keys must be set via CLI or env vars)

---

## Memory System

Three layers:
- **Identity** (`.personal/identity.md`) — static, who you are
- **Context** (`.personal/context.md`) — slow-moving, current projects/priorities
- **Memory** (`.personal/memory/`) — dynamic:
  - `short_term.md` — timestamped entries, auto-pruned to 100 lines
  - `long_term.md` — durable facts promoted by Haiku evaluation
  - `scratch.md` — sub-agent working memory, cleared after user notification

Haiku gets compressed context (identity + context + last 20 short-term lines).
Sub-agents get full context (everything).

---

## Safety and Guardrails

- **Tool scoping:** `--allowedTools` per skill config
- **Turn limits:** `--max-turns` per skill config
- **Timeouts:** Hard kill on sub-agents exceeding `timeout_seconds`
- **Concurrency cap:** Max 2 concurrent sub-agents (semaphore + queue notification)
- **No secrets downstream:** Orchestrator never passes API keys to sub-agents
- **Memory caps:** short_term.md auto-pruned to 100 lines on heartbeat
- **Single-user lockdown:** Telegram user ID auth check on all message handlers
- **Cost logging:** All Haiku and sub-agent calls logged to `.personal/costs.log`

---

## Running

```bash
# First time
pip install -r requirements.txt
python setup.py

# One-time: install bot + cloudflared as launchd services (auto-start on login)
bash bin/setup_launchd.sh

# Day-to-day management
bin/bot start|stop|restart|status|logs|tail

# Direct invocation (no auto-restart)
python agent/main.py
```

The bot runs as a launchd Launch Agent (`com.chorgi.bot`) — auto-starts on login, restarts on crash with a 5s delay. cloudflared runs as a system daemon installed by `cloudflared service install`. Logs to `~/.chorgi_bot.log`.

Environment variables (`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`) override `.personal/secrets.env` values.

---

## Webhook System

A threaded HTTP server (`agent/webhook.py`) receives external webhooks and triggers skills. Started automatically in `post_init()` if `WEBHOOK_SECRET` is set.

- **URL format:** `POST https://<domain>/<WEBHOOK_SECRET>/fathom`
- **Health check:** `GET https://<domain>/<WEBHOOK_SECRET>/health`
- **Port:** `WEBHOOK_PORT` (default 8443), exposed via cloudflared tunnel

**Fathom integration flow:**
1. Fathom sends meeting transcript webhook → signature verified (HMAC-SHA256, Svix format)
2. Transcript formatted as text → saved to `skills/fathom/workspace/<date>_<title>.txt`
3. Fathom skill triggered → Claude Code summarizes transcript (2-4 bullet points)
4. Summary sent to user via Telegram

Required env vars: `WEBHOOK_SECRET`, `FATHOM_WEBHOOK_SECRET`. Optional: `WEBHOOK_PORT`.

---

## Email Skill

The `email` skill manages email via `chorgibot@gmail.com` using stdlib IMAP/SMTP. Architecture:

- **`email_client.py`** — shared library (stdlib-only) with IMAP/SMTP functions: fetch_unread, fetch_recent, search_emails, read_email, send_email, send_html_email, list_folders, check_new_emails
- **`email_cli.py`** — CLI wrapper the sub-agent calls via Bash: `check`, `read`, `search`, `send`, `draft`, `list-drafts`, `send-draft`, `folders`
- **Draft workflow** — drafts saved as JSON in `workspace/drafts/`, reviewed by user, then sent via `send-draft`
- **Email notifications** — scheduler's `_check_emails()` polls every heartbeat (5 min), uses UID tracking (`workspace/email_seen.json`) to avoid duplicates, sends Telegram notifications for new unseen emails

Required env vars: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (Gmail App Password, 16-char with spaces).

---

## Calendar Skill

The `calendar` skill manages Google Calendar events and provides smart scheduling. Architecture:

- **`calendar_client.py`** — Google Calendar API wrapper using OAuth2 user credentials: list_events, create_event, update_event, delete_event, find_free_slots, check_conflicts
- **`calendar_cli.py`** — CLI wrapper the sub-agent calls via Bash: `list`, `free`, `create`, `update`, `delete`, `suggest`
- **`scheduler.py`** — Smart scheduling engine that scores time slots based on task type, importance, deadlines, and user preferences
- **`preferences.md`** — Editable user scheduling preferences (working hours, task type → time-of-day mapping, defaults)
- **Two calendars**: read-only access to user's private calendar (availability), full access to chorgibot@gmail.com calendar (event creation)
- **Auth**: OAuth2 user credentials (one-time browser auth, refresh token stored in `token.json`)
- **Conflict protection**: `create` command checks both calendars and refuses to schedule on busy time (override with `--force`)

Required env vars: `CALENDAR_OWNER_ID` (user's calendar ID), `CALENDAR_BOT_ID` (chorgibot@gmail.com).
Required files: `oauth_credentials.json` (OAuth client ID from GCP console), `token.json` (auto-created on first auth).
Dependencies: `google-auth`, `google-auth-oauthlib`, `google-api-python-client`.
