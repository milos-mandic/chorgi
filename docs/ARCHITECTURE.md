# Architecture Reference

Detailed module reference for the chorgi bot. For a high-level overview, see the root CLAUDE.md.

## Module Reference

### agent/api_client.py
Stdlib-only HTTP client for Anthropic Messages API. No SDK dependency.
- _call_messages_sync(system, messages, max_tokens, model) — blocking urllib.request call
- Retries transient failures (URLError, 429/5xx/529) 3x with exponential backoff + jitter, honoring Retry-After; other 4xx fail immediately
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
- promote_to_long_term(haiku_fn) — DEPRECATED, disabled pending M2 removal; the knowledge layer is the durable store

### agent/knowledge/ (durable store)
SQLite-backed knowledge layer at `.personal/knowledge.db`.
- db.py — connect() + run_migrations() (applies schema/*.sql in lex order, tracked in _migrations)
- models.py — data access: people, interactions (+participants), tasks, inbox_items (proposal queue with accept/reject), wiki_topics (+bookmark links)
- ids.py — ULID generation (Crockford base32, monotonic within process)
- content.py — markdown file conventions under .personal/content/ (people, meetings) + atomic writes
- wiki.py — clusters bookmarks into topics and synthesizes per-topic articles via Haiku; run_maintenance() has an in-flight guard + 10-min cooldown (shared by the daily schedule and the dashboard button)
- seed.py — CLI to bulk-import people from a markdown template

### agent/webhook.py
HTTP plumbing on a daemon thread (ThreadingHTTPServer, default port 8443).
- Secret-prefixed machine routes: /<WEBHOOK_SECRET>/health, /<WEBHOOK_SECRET>/fathom
- Fathom: HMAC-SHA256 (Svix format) verification → save transcript → stage meeting content → trigger post_meeting skill
- Serves the dashboard UI (agent/ui/) and dispatches /api/* to api_handlers
- Serves social post images at /social-images/<name> (name validated by social_cli.image_path; content-hashed, cached privately)
- Request bodies are capped at 1MB, or 25MB for /api/social/import and /api/social/posts/<id>/image (base64 images); a larger Content-Length gets 413
- SSE streaming for local chat lives here (needs raw socket access)
- On port-bind failure, queues a startup warning the heartbeat delivers via Telegram

### agent/api_handlers.py
Dashboard JSON API as pure (status, payload) functions.
- api_get(path) / api_write(path, method, body, server) — route dispatch
- State + mutations for tasks/bookmarks (shares the skill CLIs' write path under _data_lock + skills/_shared.file_lock), inbox accept/reject, wiki topics, local chat conversations, /api/trigger for sub-agents
- /api/social/* — a thin mapping onto skills/social/social_cli.py (create/patch/delete, image attach/remove, move, import preview/apply); social_cli.SocialError carries the HTTP status (400/404/409)

### agent/ui/
Vanilla JS dashboard (index.html, app.js, style.css) served by webhook.py.
Tabs: tasks week calendar, social posts board, watch, shopping, contacts, chat (plus
tab-less wiki and inbox pages). Bookmarks have no tab of their own; they surface through
the wiki. The /api/bookmarks routes remain.
The social board is Mon–Sun columns × one row per platform (LinkedIn, Substack Notes),
at most one post per cell. Dragging a card to another cell POSTs /api/social/move; the
post keeps its time of day, and an occupied target cell swaps the two posts. The editor
copies text with the Clipboard API (a hidden-textarea fallback), never trimming it, and
uploads images as base64 data URIs. "Import JSON" previews a contract document (new /
replace / conflict / error per post) before applying it all or nothing.
The task board is Mon–Sun only (no Pending column); the heartbeat's task rollover
keeps every open task on a day. Task cards drag between days and to a position within a day. A drop PATCHes the
destination column's whole id list as `order`; the server assigns `sort_order`
0..n-1 from it. Done cards are pinned to the bottom of every column regardless of
`sort_order`, and the drop position is clamped so that stays true. Dragging sends
`sync_calendar:false` — the board never writes to Google Calendar. In the task
editor, only the "Add to calendar" toggle (`calendar: true|false`) creates or
deletes an event; setting a time alone never does (api_handlers._apply_schedule).
`calendar_at` records the booked event time so a save with the toggle on moves an
event that a drag left behind.
Auth handled at the edge by Cloudflare Access (soft-warn locally).

### agent/local_chat.py
Standalone chat against a local LLM (OpenAI-compatible endpoint, e.g. MLX server).
- Conversations stored in .personal/local_chat/*.json
- stream_completion() yields deltas for the SSE endpoint
- Required: LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL (optional LOCAL_LLM_API_KEY)

### agent/orchestrator.py
Central coordinator.
- classify(message, user_id) — auth check, Haiku classify, route
- execute_sub_agent(classification) — spawn Claude Code, log cost
- reload_skills() — compare fingerprints, rebuild router prompt
- trigger_webhook_skill(skill, task) — webhook-triggered sub-agent
- _save_schedule() — validates via scheduler.validate_schedule before writing
- startup_warnings — list flushed to the user by the heartbeat

### agent/scheduler.py
- Scheduler(orchestrator) — heartbeat loop every 300s
- _heartbeat() — flush startup warnings, prune, check scratch, reload skills, check emails, bookmark digest, wiki sweep, task rollover (task_cli.roll_over: open tasks dated before this Monday → this Monday with carry_count+1, undated → today; calendar events untouched), social nudges (_nudge_social_posts: each unposted post whose time passed within the last 12h gets a Telegram header, then its text verbatim via orchestrator.send_raw_to_user, then its image; marked nudged once the text is delivered)
- _check_schedules() — scan schedules/*.json, evaluate triggers, execute; each file isolated so one malformed schedule can't abort the pass
- validate_schedule(schedule) — schema check + string-int coercion, used at save time

### agent/main.py
Entry point. Loads secrets, creates Orchestrator, builds Telegram Application.
- Handler order: ConversationHandler, text MessageHandler, voice MessageHandler
- post_init wires orchestrator.send_to_user (markdown-stripped), send_raw_to_user (verbatim, for copyable content) and send_photo_to_user
- Logging: RotatingFileHandler ~/.chorgi_bot.log (10MB x 5); launchd captures crash stderr to ~/.chorgi_bot.stderr.log; httpx capped at WARNING

## Skills

### skills/_shared.py
JSON persistence helpers shared by skill CLIs and the agent process.
- save_json — atomic (tmp + os.replace)
- file_lock(path) — cross-process flock on a sidecar .lock file; CLIs hold it for the whole command, agent-side mutations hold it across each RMW
- locked_json(path, default) — RMW context manager
- parse_local_datetime(value) — 'YYYY-MM-DD HH:MM[:SS]' (optional offset) → aware LOCAL_TZ datetime; used by the tasks and social skills

### Email Skill
- email_client.py — stdlib-only IMAP/SMTP functions
- email_cli.py — CLI: check, read, search, send, draft, list-drafts, send-draft, folders
- Required: GMAIL_ADDRESS, GMAIL_APP_PASSWORD

### Calendar Skill
- calendar_client.py — Google Calendar API wrapper using OAuth2
- calendar_cli.py — CLI: list, free, create, update, delete, suggest
- Two calendars: read-only user, full access bot
- Required: CALENDAR_OWNER_ID, CALENDAR_BOT_ID

### Tasks Skill
- task_cli.py — add/list/done/remove/update/pending-json/clear-done/free-slots
- Scheduling is decided at task-creation time: `--scheduled-at` creates the linked calendar event immediately; `free-slots` lets the agent pick a real open slot for loose requests. No batch/auto planner.
- Deliberate cross-skill dependency: scheduling commands subprocess calendar_cli.py and parse its JSON stdout

### Social Skill
- social_cli.py — storage + CLI (schema, validate, import [--dry-run] [--overwrite], list, get, set-status) over workspace/posts.json and workspace/images/; the dashboard imports the same functions
- schema/social_posts.v1.schema.json — the batch-import contract (JSON Schema 2020-12); validate_doc() is a stdlib validator that reads its enums/limits from that file, and also rejects duplicate platform+day slots, whitespace-only text, bad base64 and image bytes that don't match the declared type
- Invariants: one post per platform per Berlin calendar day; text stored byte-exact; scheduled_at stored as Berlin ISO with offset; LinkedIn text ≤ 3000 code points
- Images are content-hash-named files, deleted when replaced or when their post is deleted; backed up nightly (backup.py → social_images/)

### Post-Meeting Skill
- Triggered by the Fathom webhook with a pre-allocated interaction_id + transcript path
- Records the interaction in the knowledge DB and proposes inbox items (tasks, people) for accept/reject in the dashboard

### Others
- fathom — transcript archive search/read (legacy path, still written on webhook)
- bookmarks — bookmarks_cli.py over workspace/bookmarks.json (shared with agent/bookmarks.py and the dashboard)
- research — daily briefing generation + topic/source management
- linkedin — content planning/drafting/tracking (argparse CLI over workspace JSON files)
- general — catch-all

## Tests

Stdlib unittest, no network, temp dirs only:

```bash
python3 -m unittest discover tests
```

Covers: scheduler triggers + validation, _shared atomic/locked JSON (incl. multiprocess flock contention), knowledge models CRUD, ULIDs, api_client retry, linkedin_cli parser, social_cli (exact-text round trip, slot conflicts, move/swap incl. DST, images, contract validation, import preview/apply, nudges) and the /api/social routes, webhook body limits, backup.
