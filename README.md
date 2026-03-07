# Agent Harness

A personal assistant agent that talks to you via Telegram, responds instantly via Haiku, and spawns Claude Code sub-agents for anything that requires real work.

Pull it from GitHub, run the setup, and you have a working agent. You configure it to be *your* agent by filling in a few markdown files. Adding new skills is dropping a folder into `/skills/`.

## Requirements

- **Python 3.11+**
- **Claude Code CLI** — [install instructions](https://docs.anthropic.com/en/docs/claude-code)
- **Telegram bot token** — create one via [@BotFather](https://t.me/BotFather)
- **Anthropic API key** — from [console.anthropic.com](https://console.anthropic.com/)

## Setup

```bash
git clone <repo-url> && cd agent-harness
pip install -r requirements.txt
python setup.py
```

The setup script creates `.personal/` with your identity, context, and API keys.

```bash
python agent/main.py
```

## How It Works

1. You send a message via Telegram
2. Haiku classifies it in ~1s — simple questions get answered immediately
3. Complex tasks spawn a Claude Code sub-agent with the right skill
4. The agent acknowledges ("On it."), does the work, and sends the result

## Project Structure

```
agent/          # Core harness (generic, committed)
  main.py       # Entry point — Telegram bot + scheduler
  orchestrator.py  # Message routing, sub-agent lifecycle
  haiku.py      # Haiku fast-path classification
  spawner.py    # Claude Code process launcher
  scheduler.py  # Heartbeat + scheduled tasks
  memory.py     # Context and memory management
  skill_registry.py  # Auto-discovers skills

skills/         # Skill modules
  general/      # Ships with the repo — starter skill
    CLAUDE.md   # Skill knowledge (Claude Code reads this)
    config.json # Router metadata
    workspace/  # Sub-agent scratch directory

.personal/      # Your config (gitignored)
  identity.md   # Who you are
  context.md    # Current projects
  secrets.env   # API keys
  memory/       # Persistent memory store
  costs.log     # API usage tracking

schedules/      # Scheduled task definitions
templates/      # Templates for .personal/ files
```

## Adding Skills

A skill is a folder in `/skills/` with two files:

**CLAUDE.md** — what the sub-agent knows and how it behaves:
```markdown
# Research Agent
You are a research sub-agent. Your job is to find accurate information.
## Output Format
Lead with the answer, then supporting detail. Include source URLs.
```

**config.json** — router metadata:
```json
{
  "name": "research",
  "description": "Research agent. Handles questions requiring current information.",
  "tools": ["Bash", "Read", "Write", "WebSearch", "WebFetch"],
  "max_turns": 15,
  "timeout_seconds": 120
}
```

Drop the folder in, restart the bot. The skill registry auto-discovers it.

**Test a skill standalone:**
```bash
echo "Test this skill" | claude --print --cwd skills/research/
```

## Scheduled Tasks

JSON files in `/schedules/` define proactive behaviors:

```json
{
  "name": "morning_briefing",
  "trigger": "daily",
  "at_hour": 7,
  "type": "sub_agent",
  "skill": "general",
  "prompt": "Check the news. Give me a 3-bullet briefing.",
  "notify_user": true
}
```

Supported triggers: `"daily"` (with `at_hour`) and `"interval"` (with `interval_minutes`).

## Cost Logging

API usage is logged to `.personal/costs.log` in CSV format:

```
timestamp,event_type,input_tokens=N,output_tokens=N
```

Events: `haiku_classify`, `haiku_query`, `sub_agent`, `scheduled_task`.

## Customization

- **Identity:** Edit `.personal/identity.md` — who you are, tone preferences
- **Context:** Edit `.personal/context.md` — current projects, priorities
- **Memory:** The agent maintains short-term and long-term memory automatically
- **Skills:** Add folders to `/skills/` for specialized capabilities
- **Schedules:** Add JSON files to `/schedules/` for proactive tasks

## License

MIT
