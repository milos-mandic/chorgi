"""Central coordinator — routes messages, manages sub-agents."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from agent.api_client import call_haiku
from agent.skill_registry import discover_skills, build_router_prompt, get_skills_fingerprint
from agent.haiku import classify_and_respond
from agent.spawner import spawn_sub_agent
from agent.memory import Memory
from agent import bookmarks

logger = logging.getLogger(__name__)

PERSONAL_DIR = Path(__file__).parent.parent / ".personal"
SCHEDULES_DIR = Path(__file__).parent.parent / "schedules"
MAX_CONCURRENT = 4
MAX_HISTORY = 20


class Orchestrator:
    def __init__(self, authorized_user_id: str):
        self.authorized_user_id = authorized_user_id
        try:
            self.skills = discover_skills()
            self.router_prompt = build_router_prompt(self.skills)
            self._skills_fingerprint = get_skills_fingerprint(self.skills)
        except Exception as e:
            logger.error(f"Skill discovery failed: {e}")
            self.skills = {}
            self.router_prompt = build_router_prompt({})
            self._skills_fingerprint = {}
        self.history: list[dict] = []
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        self.memory = Memory(PERSONAL_DIR)
        self.send_to_user = None  # Set by main.py after bot is ready

        logger.info(f"Discovered skills: {list(self.skills.keys())}")

    def _add_to_history(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]

    def _log_cost(self, event_type: str, **kwargs):
        """Append a cost entry to .personal/costs.log."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        extras = ",".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)
        line = f"{ts},{event_type},{extras}\n"
        try:
            cost_path = PERSONAL_DIR / "costs.log"
            with open(cost_path, "a") as f:
                f.write(line)
        except OSError:
            pass  # Non-critical

    async def classify(self, message: str, user_id: str) -> dict:
        """Classify a message via Haiku. Fast path returns response inline."""
        if str(user_id) != str(self.authorized_user_id):
            return {"type": "rejected", "response": "Unauthorized."}

        try:
            context = self.memory.get_haiku_context()
        except Exception as e:
            logger.warning(f"Failed to get Haiku context: {e}")
            context = ""

        self._add_to_history("user", message)

        try:
            classification = await classify_and_respond(
                message, self.history, context, self.router_prompt
            )
        except Exception as e:
            logger.error(f"Haiku classification failed: {e}")
            return {"type": "error", "response": "Something went wrong with classification."}

        usage = classification.pop("_usage", {})
        self._log_cost("haiku_classify", input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))

        route = classification.get("route", "haiku")

        if route == "haiku":
            response = classification.get("response", "I'm not sure how to respond.")
            # Check for URLs to bookmark
            bookmark_note = await self._handle_bookmark_url(message)
            if bookmark_note:
                response = f"{response}\n\n{bookmark_note}"
            self._add_to_history("assistant", response)
            return {"type": "haiku", "response": response}

        if route == "schedule":
            schedule_data = classification.get("schedule", {})
            ok, detail = self._save_schedule(schedule_data)
            if ok:
                response = classification.get("response", detail)
            else:
                response = f"Failed to save schedule: {detail}"
            self._add_to_history("assistant", response)
            return {"type": "haiku", "response": response}

        # Sub-agent route — normalize single/multi-skill into tasks list
        ack = classification.get("ack", "On it.")
        if "skills" in classification and isinstance(classification["skills"], list):
            tasks = [{"skill": t.get("skill", "general"), "summary": t.get("summary", message)}
                     for t in classification["skills"]]
        else:
            tasks = [{"skill": classification.get("skill", "general"),
                      "summary": classification.get("summary", message)}]
        return {
            "type": "sub_agent",
            "tasks": tasks,
            "ack": ack,
            "message": message,
        }

    async def execute_sub_agent(self, classification: dict) -> dict:
        """Execute sub-agent task(s) from a prior classify() result.

        Handles both single-task and multi-task classifications via the
        normalized 'tasks' list. Backward-compatible: also accepts legacy
        'skill'/'summary' keys.
        """
        return await self.execute_sub_agents(classification)

    async def execute_sub_agents(self, classification: dict) -> dict:
        """Execute one or more sub-agent tasks in parallel."""
        try:
            # Support both new 'tasks' list and legacy 'skill'/'summary' keys
            tasks = classification.get("tasks")
            if not tasks:
                tasks = [{"skill": classification.get("skill", "general"),
                          "summary": classification.get("summary", classification.get("message", ""))}]

            full_context = self.memory.get_full_context()
            responses = []

            async def _run_one(task_info):
                skill_name = task_info["skill"]
                summary = task_info["summary"]
                skill_config = self.skills.get(skill_name) or self.skills.get("general")
                if not skill_config:
                    return skill_name, "No skills available.", True
                result = await self._spawn_with_limit(skill_config, summary, full_context, self.send_to_user)
                elapsed = result.get("elapsed_s")
                self._log_cost("sub_agent", skill=skill_name, elapsed_s=elapsed)
                if result.get("error"):
                    return skill_name, f"Error: {result['message']}", True
                return skill_name, result.get("text", "Done, but no output was returned."), False

            results = await asyncio.gather(
                *[_run_one(t) for t in tasks],
                return_exceptions=True,
            )

            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    skill_name = tasks[i]["skill"]
                    responses.append(f"[{skill_name}] Error: {res}")
                    logger.error(f"Sub-agent {skill_name} raised exception: {res}", exc_info=res)
                else:
                    skill_name, text, _is_error = res
                    responses.append(text)
                    preview = text[:200]
                    self.memory.append_short_term(f"Task: {tasks[i]['summary']} → Result: {preview}")

            if len(responses) == 1:
                combined = responses[0]
            else:
                combined = "\n\n---\n\n".join(responses)

            self._add_to_history("assistant", combined)
            return {"type": "sub_agent", "response": combined}
        except Exception as e:
            logger.error(f"Sub-agent execution failed: {e}", exc_info=True)
            return {"type": "error", "response": f"Sub-agent error: {e}"}

    async def handle_message(self, message: str, user_id: str) -> dict:
        """Convenience method — classify and execute in one call."""
        classification = await self.classify(message, user_id)
        if classification["type"] == "sub_agent":
            return await self.execute_sub_agent(classification)
        return classification

    async def _spawn_with_limit(self, skill_config, task, context, notify_fn=None) -> dict:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=5)
        except asyncio.TimeoutError:
            if notify_fn:
                try:
                    await notify_fn("I'm working on a couple things — your task is queued.")
                except Exception:
                    pass
            await self._semaphore.acquire()
        try:
            return await spawn_sub_agent(skill_config, task, context)
        finally:
            self._semaphore.release()

    # --- Helper methods for scheduler ---

    async def haiku_query(self, prompt: str) -> str:
        """Standalone Haiku call — returns raw text response."""
        text, usage = await call_haiku(
            system="",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        self._log_cost("haiku_query", input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
        return text

    async def run_scheduled_task(self, skill: str, task: str) -> str:
        """Spawn a sub-agent for a scheduled task. Returns response text."""
        skill_config = self.skills.get(skill)
        if not skill_config:
            skill_config = self.skills.get("general")
        if not skill_config:
            return "No skills available."

        full_context = self.memory.get_full_context()
        result = await self._spawn_with_limit(skill_config, task, full_context, self.send_to_user)

        elapsed = result.get("elapsed_s")
        self._log_cost("scheduled_task", skill=skill, elapsed_s=elapsed)

        if result.get("error"):
            return f"Error: {result['message']}"
        return result.get("text", "Done, but no output was returned.")

    async def trigger_webhook_skill(self, skill: str, task: str) -> str:
        """Spawn a sub-agent for a webhook-triggered task. Sends result to user."""
        skill_config = self.skills.get(skill)
        if not skill_config:
            skill_config = self.skills.get("general")
        if not skill_config:
            msg = "No skills available for webhook task."
            logger.error(msg)
            return msg

        full_context = self.memory.get_full_context()
        result = await self._spawn_with_limit(skill_config, task, full_context, self.send_to_user)

        elapsed = result.get("elapsed_s")
        self._log_cost("webhook_task", skill=skill, elapsed_s=elapsed)

        if result.get("error"):
            response_text = f"Error: {result['message']}"
        else:
            response_text = result.get("text", "Done, but no output was returned.")

        self.memory.append_short_term(f"Webhook ({skill}): {task[:100]} → {response_text[:200]}")

        if self.send_to_user:
            try:
                await self.send_to_user(f"Fathom meeting summary:\n\n{response_text}")
            except Exception as e:
                logger.error("Failed to send webhook result to user: %s", e)

        return response_text

    async def reload_skills(self):
        """Re-discover skills if any were added, removed, or modified."""
        try:
            new_skills = discover_skills()
            new_fingerprint = get_skills_fingerprint(new_skills)
        except Exception as e:
            logger.error(f"Skill reload failed: {e}")
            return

        if new_fingerprint == self._skills_fingerprint:
            return

        old_names = set(self._skills_fingerprint.keys())
        new_names = set(new_fingerprint.keys())
        added = new_names - old_names
        removed = old_names - new_names
        updated = {n for n in old_names & new_names
                   if self._skills_fingerprint[n] != new_fingerprint[n]}

        self.skills = new_skills
        self.router_prompt = build_router_prompt(new_skills)
        self._skills_fingerprint = new_fingerprint

        changes = []
        if added:
            changes.append(f"added: {', '.join(added)}")
        if removed:
            changes.append(f"removed: {', '.join(removed)}")
        if updated:
            changes.append(f"updated: {', '.join(updated)}")
        logger.info(f"Skills hot-reloaded — {'; '.join(changes)}")

    def _save_schedule(self, schedule: dict) -> tuple:
        """Sanitize and write a schedule JSON to schedules/. Returns (ok, detail)."""
        name = schedule.get("name", "")
        if not name:
            return False, "Schedule must have a name."
        # Sanitize to safe filename
        safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
        if not safe_name:
            return False, "Invalid schedule name."

        SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
        path = SCHEDULES_DIR / f"{safe_name}.json"

        # Remove internal fields, keep only schedule definition
        to_save = {k: v for k, v in schedule.items()
                   if k in ("name", "trigger", "at_hour", "interval_minutes",
                            "type", "skill", "prompt", "notify_user")}
        try:
            path.write_text(json.dumps(to_save, indent=2) + "\n")
        except OSError as e:
            return False, str(e)

        trigger = to_save.get("trigger", "unknown")
        if trigger == "daily":
            timing = f"daily at {to_save.get('at_hour', '?')}:00 UTC"
        elif trigger == "interval":
            timing = f"every {to_save.get('interval_minutes', '?')} minutes"
        else:
            timing = trigger
        return True, f"Schedule '{safe_name}' saved ({timing})."

    async def check_scratch_pad(self):
        """Read scratch.md; if non-empty, notify user and clear."""
        content = self.memory.read_scratch()
        if content and content.strip():
            if self.send_to_user:
                await self.send_to_user(f"Scratch pad note:\n\n{content.strip()}")
            self.memory.clear_scratch()

    # --- Bookmarks ---

    async def _handle_bookmark_url(self, message: str) -> str | None:
        """Detect URLs in message, fetch metadata, summarize, store. Returns note or None."""
        urls = bookmarks.extract_urls(message)
        if not urls:
            return None

        parts = []
        for url in urls:
            meta = await asyncio.to_thread(bookmarks.fetch_page_meta, url)
            title = meta.get("title") or url
            description = meta.get("description", "")

            # Ask Haiku for a 1-2 sentence summary
            summary_prompt = (
                f"Summarize this page in 1-2 sentences.\n"
                f"Title: {title}\nDescription: {description}\nURL: {url}"
            )
            try:
                summary = await self.haiku_query(summary_prompt)
            except Exception as e:
                logger.warning(f"Bookmark summary failed for {url}: {e}")
                summary = description or "No summary available."

            unsent_count = bookmarks.add_bookmark(url, title, summary)
            parts.append(f"Saved! **{title}**\n{summary}")

            self.memory.append_short_term(f"Bookmarked: {title} — {url}")

        # Check digest threshold
        digest_note = ""
        unsent = bookmarks.get_unsent_bookmarks()
        if len(unsent) >= 5:
            digest_result = await self._send_bookmark_digest()
            if digest_result:
                digest_note = f"\n\n{digest_result}"
        else:
            digest_note = f"\n({len(unsent)}/5 until digest)"

        return "\n\n".join(parts) + digest_note

    async def _send_bookmark_digest(self) -> str | None:
        """Email all unsent bookmarks as a digest. Returns status message or None."""
        unsent = bookmarks.get_unsent_bookmarks()
        if not unsent:
            return None

        # Build HTML email
        items_html = []
        for b in unsent:
            items_html.append(
                f'<li><a href="{b["url"]}">{b.get("title") or b["url"]}</a>'
                f'<br><em>{b.get("summary", "")}</em></li>'
            )
        html_body = (
            f"<h2>Reading Digest — {len(unsent)} links</h2>"
            f"<ul>{''.join(items_html)}</ul>"
            f"<p><small>Sent by Chorgi Bot</small></p>"
        )

        try:
            import sys
            email_dir = str(Path(__file__).parent.parent / "skills" / "email")
            if email_dir not in sys.path:
                sys.path.insert(0, email_dir)
            import email_client

            gmail_addr = os.environ.get("GMAIL_ADDRESS", "")
            await asyncio.to_thread(
                email_client.send_html_email,
                gmail_addr,
                f"Reading Digest — {len(unsent)} links",
                html_body,
            )
            bookmarks.mark_emailed([b["url"] for b in unsent])
            self._log_cost("bookmark_digest", count=len(unsent))
            msg = f"Sent your reading digest ({len(unsent)} links) to email!"
            if self.send_to_user:
                await self.send_to_user(msg)
            return msg
        except Exception as e:
            logger.error(f"Failed to send bookmark digest: {e}")
            return f"Failed to send digest: {e}"
