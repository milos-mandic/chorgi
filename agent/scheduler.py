"""Scheduler — heartbeat loop and scheduled task execution."""

import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEDULES_DIR = Path(__file__).parent.parent / "schedules"
HEARTBEAT_INTERVAL = 300  # 5 minutes

# Add email skill to path for direct import (stdlib-only, no sub-agent needed)
_EMAIL_SKILL_DIR = Path(__file__).parent.parent / "skills" / "email"
if str(_EMAIL_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_EMAIL_SKILL_DIR))


class Scheduler:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    async def start(self):
        """Infinite loop: heartbeat → check schedules → sleep."""
        logger.info("Scheduler started")
        while True:
            try:
                await self._heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

            try:
                await self._check_schedules()
            except Exception as e:
                logger.error(f"Schedule check error: {e}")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _heartbeat(self):
        """Periodic maintenance: prune, promote, check scratch."""
        logger.info("Heartbeat running")
        memory = self.orchestrator.memory

        await memory.prune_short_term()
        await memory.promote_to_long_term(self.orchestrator.haiku_query)

        # One-time dedup of long_term.md (delete .dedup_done to re-trigger)
        dedup_flag = memory.memory_dir / ".dedup_done"
        if not dedup_flag.exists():
            try:
                await memory.deduplicate_long_term(self.orchestrator.haiku_query)
                dedup_flag.touch()
                logger.info("One-time long_term.md dedup complete")
            except Exception as e:
                logger.error(f"Long-term dedup failed: {e}")

        await self.orchestrator.check_scratch_pad()
        await self.orchestrator.reload_skills()
        await self._check_emails()
        await self._check_bookmark_digest()

        logger.info("Heartbeat complete")

    async def _check_schedules(self):
        """Scan schedules/*.json, evaluate triggers, execute due tasks."""
        if not SCHEDULES_DIR.exists():
            return

        now = datetime.now(timezone.utc)

        for path in sorted(SCHEDULES_DIR.glob("*.json")):
            try:
                schedule = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Bad schedule file {path.name}: {e}")
                continue

            if self._is_due(schedule, now):
                try:
                    await self._execute(schedule)
                    self._mark_ran(path, now)
                except Exception as e:
                    name = schedule.get('name', path.name)
                    logger.error(f"Schedule {name} failed: {e}")
                    if self.orchestrator.send_to_user:
                        try:
                            await self.orchestrator.send_to_user(f"⚠️ Scheduled task '{name}' failed:\n{e}")
                        except Exception:
                            pass

    def _is_due(self, schedule: dict, now: datetime) -> bool:
        trigger = schedule.get("trigger")
        last_run_str = schedule.get("last_run")

        if last_run_str:
            last_run = datetime.fromisoformat(last_run_str)
        else:
            last_run = None

        if trigger == "daily":
            at_hour = schedule.get("at_hour", 8)
            if now.hour < at_hour:
                return False
            if last_run and last_run.date() == now.date():
                return False  # Already ran today
            return True

        elif trigger == "interval":
            interval_min = schedule.get("interval_minutes", 60)
            interval_sec = interval_min * 60
            if last_run is None:
                return True
            elapsed = (now - last_run).total_seconds()
            return elapsed >= interval_sec

        return False

    async def _execute(self, schedule: dict):
        name = schedule.get("name", "unnamed")
        task_type = schedule.get("type", "haiku")
        prompt = schedule.get("prompt", "")
        notify = schedule.get("notify_user", False)
        silent_empty = schedule.get("silent_when_empty", False)

        logger.info(f"Executing schedule: {name}")

        if task_type == "sub_agent":
            skill = schedule.get("skill", "general")
            result = await self.orchestrator.run_scheduled_task(skill, prompt)
        else:
            result = await self.orchestrator.haiku_query(prompt)

        is_error = isinstance(result, str) and result.startswith("Error:")
        if is_error:
            logger.warning(f"Schedule {name} returned error: {result}")

        if notify and self.orchestrator.send_to_user:
            if silent_empty and isinstance(result, str) and not result.strip():
                logger.info(f"Schedule {name}: empty result, skipping notification")
            elif is_error:
                display = schedule.get("display_name") or name.replace("_", " ").title()
                await self.orchestrator.send_to_user(f"⚠️ {display} failed:\n{result}")
            else:
                display = schedule.get("display_name") or name.replace("_", " ").title()
                await self.orchestrator.send_to_user(f"{display}\n\n{result}")

        logger.info(f"Schedule {name} completed")

    async def _check_emails(self):
        """Poll for new unseen emails and notify user via Telegram."""
        try:
            import email_client
            from forward_parser import is_forwarded_from_milos
            new = await asyncio.to_thread(email_client.check_new_emails)
            if not new:
                return
            for e in new:
                try:
                    subject = e.get("subject", "")
                    # Skip calendar RSVP notifications (Accepted/Declined/Tentative)
                    if re.match(r"^(Accepted|Declined|Tentative):", subject):
                        logger.debug(f"Skipping calendar RSVP email: {subject}")
                        continue
                    if is_forwarded_from_milos(e):
                        await self._handle_forwarded_email(e)
                    else:
                        sender = e.get("from", "unknown")
                        preview = e.get("body_preview", "")
                        msg = f"\U0001f4e7 New email from {sender}\nSubject: {subject}\n{preview}"
                        if self.orchestrator.send_to_user:
                            await self.orchestrator.send_to_user(msg)
                except Exception as email_err:
                    logger.warning(f"Failed to process email '{e.get('subject', '?')}': {email_err}")
            logger.info(f"Notified user of {len(new)} new email(s)")
        except Exception as e:
            logger.warning(f"Email check failed: {e}", exc_info=True)

    async def _handle_forwarded_email(self, email_summary: dict):
        """Process a forwarded email from Milos: parse, draft reply via sub-agent, email back."""
        import email_client
        from forward_parser import MILOS_EMAIL, parse_forwarded_email

        uid = email_summary["uid"]
        subject = email_summary.get("subject", "(no subject)")

        try:
            # Fetch full email body
            full_email = await asyncio.to_thread(email_client.read_email, uid, 8000)
            body = full_email.get("body", "")
            if not body:
                raise ValueError("Empty email body")

            # Parse forwarded content
            parsed = parse_forwarded_email(body)
            if not parsed:
                raise ValueError("Could not parse forwarded email format")

            if not parsed["instructions"].strip() and not parsed["original_body"].strip():
                raise ValueError("Empty instructions and original body")

            # Build prompt for the email sub-agent
            prompt = self._build_forward_reply_prompt(parsed)

            # Spawn email sub-agent to draft and send the reply
            logger.info(f"Drafting reply for forwarded email: {subject}")
            result = await self.orchestrator.run_scheduled_task("email", prompt)

            # Notify via Telegram
            if self.orchestrator.send_to_user:
                sender_name = parsed.get("original_from_name", parsed["original_from"])
                orig_subject = parsed["original_subject"]
                await self.orchestrator.send_to_user(
                    f"\u270d\ufe0f Draft reply sent to your email\n"
                    f"To: {sender_name}\n"
                    f"Re: {orig_subject}\n\n"
                    f"{result}"
                )

            logger.info(f"Forward-reply completed for: {subject}")

        except Exception as e:
            logger.warning(f"Forward-reply failed for '{subject}': {e}")
            # Fall back to standard notification
            sender = email_summary.get("from", "unknown")
            preview = email_summary.get("body_preview", "")
            msg = f"\U0001f4e7 New email from {sender}\nSubject: {subject}\n{preview}"
            if self.orchestrator.send_to_user:
                await self.orchestrator.send_to_user(msg)
                await self.orchestrator.send_to_user(
                    f"\u26a0\ufe0f Auto-reply drafting failed: {e}"
                )

    def _build_forward_reply_prompt(self, parsed: dict) -> str:
        """Build the sub-agent prompt for drafting a forwarded email reply."""
        return (
            "## Forward-Reply Task\n\n"
            "Draft a reply to the forwarded email below using Milos's writing style.\n"
            "Read `writing_style.md` first for voice and tone guidance.\n\n"
            "### Milos's Instructions\n"
            f"{parsed['instructions']}\n\n"
            "### Original Email\n"
            f"From: {parsed['original_from']}\n"
            f"Date: {parsed['original_date']}\n"
            f"Subject: {parsed['original_subject']}\n\n"
            f"{parsed['original_body']}\n\n"
            "### What To Do\n"
            f"1. Read `writing_style.md` for Milos's voice\n"
            f"2. Draft a reply addressing {parsed['original_from_name']} by first name\n"
            f"3. Send it to milos.mandic.etf@gmail.com with subject "
            f"\"Re: {parsed['original_subject']} \u2014 Draft Reply\" using email_cli.py send\n"
            f"4. Return the draft text in your response"
        )

    async def _check_bookmark_digest(self):
        """Safety-net: send bookmark digest if 5+ unsent bookmarks accumulated."""
        try:
            from agent.bookmarks import get_unsent_bookmarks
            unsent = get_unsent_bookmarks()
            if len(unsent) >= 5:
                await self.orchestrator._send_bookmark_digest()
        except Exception as e:
            logger.debug(f"Bookmark digest check skipped: {e}")

    def _mark_ran(self, schedule_path: Path, now: datetime):
        """Update last_run in the schedule JSON file."""
        try:
            data = json.loads(schedule_path.read_text())
            data["last_run"] = now.isoformat()
            schedule_path.write_text(json.dumps(data, indent=2) + "\n")
        except Exception as e:
            logger.error(f"Failed to update last_run for {schedule_path.name}: {e}")
