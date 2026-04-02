# Chorgi Bot

Personal assistant agent harness. Telegram interface, Haiku for routing/instant responses, Claude Code sub-agents for deep work.

## Design Principles

1. **Harness is generic, config is personal.** Everything personal lives in `.personal/` (gitignored).
2. **Skills are plug-and-play.** Skill = folder with `CLAUDE.md` + `config.json` in `skills/`. Auto-discovered on heartbeat.
3. **Haiku for speed, Claude Code for depth.** Haiku routes + responds instantly. Claude Code handles tool use and multi-step work.
4. **Stdlib-first Python.** Only external dep is `python-telegram-bot`. API calls use `urllib.request` (no `anthropic` SDK).

## Key Files

- `agent/main.py` — Entry point (Telegram bot + scheduler + webhook)
- `agent/orchestrator.py` — Message routing, sub-agent lifecycle, schedule saving
- `agent/spawner.py` — Claude Code sub-agent launcher (`--cwd <skill_dir>`)
- `agent/scheduler.py` — Heartbeat loop (5 min), scheduled task execution
- `agent/memory.py` — Context assembly, short-term pruning, long-term promotion
- `agent/haiku.py` — Haiku classify+respond
- `agent/webhook.py` — Threaded HTTP server for external webhooks
- `docs/ARCHITECTURE.md` — Detailed module reference

## Message Routing

| Route | When | Response time |
|-------|------|---------------|
| `haiku` | Greetings, chitchat, simple questions | ~1-2s |
| `schedule` | "Remind me every morning at 7 to..." | ~1-2s |
| `sub_agent` | Tool use, research, code, multi-step tasks | ~15-90s |

## Skills

Each skill has: `CLAUDE.md` (behavior), `config.json` (routing metadata + tool/turn/timeout limits), `workspace/` (scratch dir). Sub-agents run with `--cwd <skill_dir>` so they read the skill's own CLAUDE.md.

Current skills: `general`, `fathom`, `email`, `calendar`, `research`, `linkedin`, `bookmarks`, `tasks`.

## Schedules

JSON files in `schedules/`. Triggers: `daily` (at_hour UTC) or `interval` (interval_minutes). Created via chat or by dropping JSON files.

## Development

```bash
bin/bot start|stop|restart|status|logs|tail
```

Always stop the bot before making changes, restart after. The bot runs as a launchd Launch Agent.

**CLAUDE.md edits:** This file gets auto-reverted mid-session. Always chain write+stage in one bash command:
`cat > CLAUDE.md << 'EOF' ... EOF && git add CLAUDE.md`

## Env Vars (`.personal/secrets.env`)

`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `OPENAI_API_KEY`, `WEBHOOK_SECRET`, `WEBHOOK_PORT`, `FATHOM_WEBHOOK_SECRET`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `CALENDAR_OWNER_ID`, `CALENDAR_BOT_ID`, `LINKEDIN_COOKIE`
