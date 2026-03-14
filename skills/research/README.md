# Research Skill

Generates daily research briefings on topics you track and sends them as HTML emails.

## How it works

1. You maintain a list of topics and preferred sources in `topics.json`
2. Every morning at 8:00 CET, the scheduler triggers this skill
3. The sub-agent searches the web for fresh articles on each topic
4. Results are formatted as an HTML email with linked titles, one-line summaries, source names, and publication dates
5. The email is sent to your `USER_EMAIL` address
6. Sent article URLs are tracked to avoid repeats across briefings

## Topic & Source Management

Via Telegram (routed automatically):
- "Add research topic: Kubernetes" → adds topic
- "Remove research topic: AI Agents" → removes topic
- "List my research topics" → shows topics and sources
- "Add research source: Financial Times" → adds source

Via CLI:
```bash
cd skills/research
python3 research_cli.py topics list
python3 research_cli.py topics add "AI Agents" "Focus on autonomous coding agents"
python3 research_cli.py topics remove "AI Agents"
python3 research_cli.py sources list
python3 research_cli.py sources add "Financial Times"
python3 research_cli.py sources remove "Bloomberg Technology"
```

---

## Setup

### 1. Email

The research skill uses the email skill (`skills/email/`) to send briefings. Make sure the email skill is configured with `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` in `.personal/secrets.env`.

### 2. User Email

Add your personal email (the recipient) to `.personal/secrets.env`:
```
USER_EMAIL=you@example.com
```

### 3. Schedule

The schedule is pre-configured at `schedules/morning_research_briefing.json`:
- **Trigger:** Daily at 06:00 UTC (08:00 CEST)
- Adjust `at_hour` when daylight saving changes (October → set to 7 for CET)

---

## Files

| File | Purpose |
|------|---------|
| `config.json` | Skill metadata for auto-discovery |
| `topics.json` | Topics and sources list |
| `research_cli.py` | CLI for topic/source CRUD and sending briefings |
| `CLAUDE.md` | Sub-agent behavior instructions |
| `workspace/briefing_draft.json` | Latest briefing draft (written by sub-agent) |
| `workspace/sent_history.json` | Tracks previously sent URLs to avoid duplicates |

---

## Manual Test

```bash
# Test topic management
cd skills/research
python3 research_cli.py topics list

# Run a full briefing (requires claude CLI)
echo "Generate the morning research briefing." | claude --print --allowedTools "Bash,Read,Write,WebSearch,WebFetch" --max-turns 30
```
