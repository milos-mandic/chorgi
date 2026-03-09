"""Auto-discovers skills from /skills/ and builds the router prompt."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "skills"


def discover_skills() -> dict:
    """Scan /skills/ and return all valid skill configs."""
    skills = {}
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        config_path = skill_dir / "config.json"
        claude_md = skill_dir / "CLAUDE.md"
        if config_path.exists() and claude_md.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                config["dir"] = str(skill_dir.resolve())
                skills[config["name"]] = config
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning(f"Skipping skill {skill_dir.name}: {e}")
                continue
    return skills


def get_skills_fingerprint(skills: dict) -> dict:
    """Return {skill_name: config.json mtime} for cheap change detection."""
    fingerprint = {}
    for name, config in skills.items():
        config_path = Path(config["dir"]) / "config.json"
        try:
            fingerprint[name] = config_path.stat().st_mtime
        except OSError:
            fingerprint[name] = 0
    return fingerprint


def build_router_prompt(skills: dict) -> str:
    """Generate the Haiku routing system prompt from discovered skills."""
    skill_lines = []
    for name, config in skills.items():
        skill_lines.append(f'- "{name}": {config["description"]}')
    skills_block = "\n".join(skill_lines)

    return f"""You are a routing classifier for a personal assistant.
Given a user message and conversation context, decide the route.

Route "haiku": greetings, simple questions, chitchat, confirmations, opinions,
anything you can answer well from your training data alone.

Route "sub_agent": anything requiring tool use, current information, file ops,
multi-step tasks, research, code, analysis, or real-world actions.
If a task requires MULTIPLE skills, list them all — they run in parallel.

Route "schedule": the user wants to create a RECURRING or REPEATING task
(e.g., "every morning", "daily", "weekly", "every 2 hours"). One-time tasks,
single events, or calendar entries go to sub_agent with the calendar skill.

Available skills:
{skills_block}

Respond ONLY with JSON. Examples by route:

haiku:
{{"route": "haiku", "response": "<your reply to the user>"}}

sub_agent (single skill):
{{"route": "sub_agent", "skill": "<skill_name>", "summary": "<one-line task>", "ack": "<brief, friendly acknowledgment of what you're about to do>"}}

sub_agent (multiple skills needed):
{{"route": "sub_agent", "skills": [{{"skill": "<skill_name>", "summary": "<task for this skill>"}}, ...], "ack": "<brief, friendly acknowledgment>"}}

The "ack" field is shown to the user immediately while the task runs.
Make it conversational and specific, e.g. "Checking your calendar and drafting that email now" not just "On it."

schedule:
{{"route": "schedule", "schedule": {{
  "name": "<snake_case_slug>",
  "trigger": "daily" | "interval",
  "at_hour": <0-23 UTC, required if daily>,
  "interval_minutes": <int, required if interval>,
  "type": "haiku" | "sub_agent",
  "skill": "<skill_name, required if type is sub_agent>",
  "prompt": "<what the agent should do>",
  "notify_user": true
}}, "response": "<confirmation message to user>"}}"""
