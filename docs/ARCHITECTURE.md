# Architecture Reference

Detailed module reference for the chorgi bot. For a high-level overview, see the root CLAUDE.md.

## Module Reference

### agent/api_client.py
Stdlib-only HTTP client for Anthropic Messages API. No SDK dependency.
- _call_messages_sync(system, messages, max_tokens, model) — blocking urllib.request call
- call_haiku(system, messages, max_tokens) — async wrapper via asyncio.to_thread

### agent/haiku.py
Single Haiku call that classifies intent and optionally responds inline.
- classify_and_respond(message, history, context, router_prompt) — returns parsed JSON dict
- Robust JSON parsing: tries raw, markdown fenced, first {...}, fallback

### agent/skill_registry.py
- discover_skills() — scans skills/*/ for dirs with config.json and CLAUDE.md
- get_skills_fingerprint(skills) — returns {name: config.json mtime}
- build_router_prompt(skills) — generates Haiku system prompt with three routes

### agent/spawner.py
- spawn_sub_agent(skill_config, task, context) — runs claude --print via asyncio.create_subprocess_exec
- Passes context + task via stdin; Claude Code reads skill CLAUDE.md from --cwd
- Strips CLAUDECODE and ANTHROPIC_API_KEY env vars

### agent/memory.py
- Memory(personal_dir) — manages all .personal/ file I/O
- get_haiku_context() — identity + context + last 20 lines of short_term
- get_full_context() — identity + context + full long_term + full short_term
- append_short_term(entry) — timestamped append
- prune_short_term() — keeps last 100 lines
- promote_to_long_term(haiku_fn) — Haiku-driven promotion

### agent/webhook.py
Threaded HTTP server for external webhooks.
- WebhookServer on daemon thread, configurable port (default 8443)
- HMAC-SHA256 signature verification (Svix format)
- Fathom transcript processing and skill triggering

### agent/orchestrator.py
Central coordinator.
- classify(message, user_id) — auth check, Haiku classify, route
- execute_sub_agent(classification) — spawn Claude Code, log cost
- reload_skills() — compare fingerprints, rebuild router prompt
- trigger_webhook_skill(skill, task) — webhook-triggered sub-agent

### agent/scheduler.py
- Scheduler(orchestrator) — heartbeat loop every 300s
- _heartbeat() — prune, promote, check scratch, reload skills, check emails
- _check_schedules() — scan schedules/*.json, evaluate triggers, execute

### agent/main.py
Entry point. Loads secrets, creates Orchestrator, builds Telegram Application.
- Handler order: ConversationHandler, text MessageHandler, voice MessageHandler

## Email Skill
- email_client.py — stdlib-only IMAP/SMTP functions
- email_cli.py — CLI: check, read, search, send, draft, list-drafts, send-draft, folders
- Required: GMAIL_ADDRESS, GMAIL_APP_PASSWORD

## Calendar Skill
- calendar_client.py — Google Calendar API wrapper using OAuth2
- calendar_cli.py — CLI: list, free, create, update, delete, suggest
- Two calendars: read-only user, full access bot
- Required: CALENDAR_OWNER_ID, CALENDAR_BOT_ID
