# Chorgi Bot

Personal assistant agent harness. Telegram interface, Haiku for routing/instant responses, Claude Code sub-agents for deep work.

## Design Principles

1. **Harness is generic, config is personal.** Everything personal lives in `.personal/` (gitignored).
2. **Skills are plug-and-play.** Skill = folder with `CLAUDE.md` + `config.json` in `skills/`. Auto-discovered on heartbeat.
3. **Haiku for speed, Claude Code for depth.** Haiku routes + responds instantly. Claude Code handles tool use and multi-step work.
4. **Stdlib-first Python.** Only external deps are `python-telegram-bot` + Google API libs. Anthropic API calls use `urllib.request` (no `anthropic` SDK).

## Key Files

- `agent/main.py` — Entry point (Telegram bot + scheduler + webhook; rotating log setup)
- `agent/orchestrator.py` — Message routing, sub-agent lifecycle, schedule saving
- `agent/spawner.py` — Claude Code sub-agent launcher (`--cwd <skill_dir>`)
- `agent/scheduler.py` — Heartbeat loop (5 min), scheduled task execution, schedule validation
- `agent/memory.py` — Context assembly, short-term pruning (long-term promotion deprecated)
- `agent/knowledge/` — SQLite durable store: people, interactions, inbox, wiki (+ Haiku clustering)
- `agent/haiku.py` — Haiku classify+respond
- `agent/api_client.py` — Anthropic HTTP client with retry/backoff
- `agent/webhook.py` — HTTP server: Fathom webhook, dashboard static + SSE chat
- `agent/api_handlers.py` — Dashboard JSON API route handlers
- `agent/ui/` — Dashboard frontend (tasks, bookmarks, wiki, inbox, contacts, chat)
- `agent/local_chat.py` — Local LLM chat backend
- `skills/_shared.py` — Atomic + flock-locked JSON helpers shared by skill CLIs
- `docs/ARCHITECTURE.md` — Detailed module reference

## Message Routing

| Route | When | Response time |
|-------|------|---------------|
| `haiku` | Greetings, chitchat, simple questions | ~1-2s |
| `schedule` | "Remind me every morning at 7 to..." | ~1-2s |
| `sub_agent` | Tool use, research, code, multi-step tasks | ~15-90s |

## Skills

Each skill has: `CLAUDE.md` (behavior), `config.json` (routing metadata + tool/turn/timeout limits), `workspace/` (data + scratch, gitignored). Sub-agents run with `--cwd <skill_dir>` so they read the skill's own CLAUDE.md. CLIs that mutate workspace JSON go through `skills/_shared.py` (atomic writes + cross-process file lock).

Current skills: `general`, `fathom`, `email`, `calendar`, `research`, `linkedin`, `bookmarks`, `tasks`, `post_meeting`.

## Schedules

JSON files in `schedules/`. Triggers: `daily` (at_hour UTC) or `interval` (interval_minutes). Types: `haiku`, `sub_agent`, `internal`. Created via chat (validated before save) or by dropping JSON files. Disabled schedules live in `schedules/disabled/`.

## Development

```bash
bin/bot start|stop|restart|status|logs|tail
python3 -m unittest discover tests   # run before restarting after changes
```

Always stop the bot before making changes, restart after. The bot runs as a launchd Launch Agent. Logs rotate in-process at `~/.chorgi_bot.log` (10MB x 5); hard-crash stderr lands in `~/.chorgi_bot.stderr.log`.

**CLAUDE.md edits:** This file gets auto-reverted mid-session. Always chain write+stage in one bash command:
`cat > CLAUDE.md << 'EOF' ... EOF && git add CLAUDE.md`

## Env Vars (`.personal/secrets.env`)

`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `OPENAI_API_KEY`, `WEBHOOK_SECRET`, `WEBHOOK_PORT`, `FATHOM_WEBHOOK_SECRET`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `CALENDAR_OWNER_ID`, `CALENDAR_BOT_ID`, `LINKEDIN_COOKIE`, `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, `LOCAL_LLM_API_KEY`
