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
│   ├── main.py                   # Entry point — Telegram bot + scheduler + onboarding handlers
│   ├── voice.py                  # Voice support — Whisper STT + OpenAI TTS (stdlib-only)
│   ├── orchestrator.py           # Message routing (3 routes), sub-agent lifecycle, schedule saving
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
│   └── phone/                    # Device control via termux-api
│       ├── CLAUDE.md             # Termux command reference
│       ├── config.json           # tools: Bash,Read,Write — timeout: 120s
│       └── workspace/            # Photos, recordings, scratch files
│
├── templates/                    # Templates for .personal/ files
│   ├── identity.md.template      # Placeholders: {name}, {role}, {style}
│   ├── context.md.template       # Placeholders: {projects}
│   └── secrets.env.template
│
├── schedules/                    # Schedule JSON files (gitignored except templates)
│
└── .personal/                    # User config (gitignored, created by setup.py or /setup)
    ├── identity.md
    ├── context.md
    ├── secrets.env               # ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_USER_ID, OPENAI_API_KEY (optional)
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
- **Termux fixes:** Ensures `/tmp` exists (Claude Code sandbox requirement) and strips the `CLAUDECODE` env var to prevent "nested session" errors when spawning sub-agents

### `agent/memory.py`
- `Memory(personal_dir)` — manages all `.personal/` file I/O
- `get_haiku_context()` — identity + context + last 20 lines of short_term (compressed)
- `get_full_context()` — identity + context + full long_term + full short_term
- `append_short_term(entry)` — timestamped append
- `prune_short_term()` — keeps last 100 lines
- `promote_to_long_term(haiku_fn)` — sends short_term to Haiku, which returns JSON with `promote` and `remove_lines` lists
- `read_scratch()` / `clear_scratch()` — scratch pad for sub-agent initiated notifications

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
- `check_scratch_pad()` — reads scratch.md, notifies user if non-empty, clears
- `_log_cost(event_type, **kwargs)` — appends to `.personal/costs.log`
- `send_to_user` — callback set by `main.py` after bot init

### `agent/scheduler.py`
- `Scheduler(orchestrator)` — heartbeat loop every 300s
- `_heartbeat()` — prune short_term → promote to long_term → check scratch pad → reload skills
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
- `post_init` — wires `send_to_user` callback, starts scheduler as background task
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

# Start the bot
python agent/main.py
```

Environment variables (`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`) override `.personal/secrets.env` values.

---

## Termux / Android Notes

This bot runs on Android via Termux. Key platform considerations:

- **`/tmp` directory:** Claude Code's sandbox requires `/tmp` to exist. Termux doesn't provide one natively, but the interactive shell alias (`proot -b $PREFIX/tmp:/tmp claude`) creates a real `/tmp` dir on `/data` that persists. The spawner calls `os.makedirs("/tmp", exist_ok=True)` to ensure it exists for sub-agents.
- **No `proot` for sub-agents:** Using `proot` to wrap sub-agent calls adds massive CPU overhead (~2x slower). Since `/tmp` persists as a real directory, sub-agents run `claude` directly without `proot`.
- **`CLAUDECODE` env var:** Claude Code sets this in its environment to detect nested sessions. The spawner strips it so sub-agents don't refuse to start. This is safe because sub-agents are independent processes, not truly nested.
- **`asyncio.create_subprocess_exec` bypasses shell aliases:** The bot spawns sub-agents via `asyncio.create_subprocess_exec`, which doesn't read `.zshrc` aliases. All Termux workarounds must be applied in `spawner.py` directly.

---

## Phone Skill

The `phone` skill enables device control via `termux-api` commands. Capabilities:

- **Hardware control:** Flashlight, vibration, brightness, volume
- **Communication:** Send/read SMS, text-to-speech, clipboard
- **Sensors:** Battery status, WiFi info, GPS location, barometer, light, proximity, temperature
- **Media:** Camera photos (front/back), microphone recording
- **Notifications:** System notification list, toast messages

Requires the `termux-api` package and the Termux:API Android app to be installed.
