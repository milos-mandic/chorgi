"""Scheduler — heartbeat loop and scheduled task execution."""

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEDULES_DIR = Path(__file__).parent.parent / "schedules"
HEARTBEAT_INTERVAL = 300  # 5 minutes
RETRY_COOLDOWN_SEC = 30 * 60  # back off after a failed/interrupted run
EMAIL_CHECK_TIMEOUT = 120  # hard cap so a wedged IMAP call can't stall the heartbeat

VALID_TRIGGERS = {"daily", "interval"}
VALID_TYPES = {"haiku", "sub_agent", "internal"}


def validate_schedule(schedule: dict) -> tuple[bool, str]:
    """Validate a schedule dict before it's written to schedules/.

    Coerces numeric fields in place (Haiku often emits them as strings).
    Returns (ok, error_message).
    """
    if not isinstance(schedule, dict):
        return False, "Schedule must be a JSON object."
    if not str(schedule.get("name") or "").strip():
        return False, "Schedule must have a name."
    trigger = schedule.get("trigger")
    if trigger not in VALID_TRIGGERS:
        return False, f"trigger must be one of: {', '.join(sorted(VALID_TRIGGERS))}."
    if trigger == "daily":
        try:
            at_hour = int(schedule.get("at_hour", 8))
        except (TypeError, ValueError):
            return False, "at_hour must be an integer hour 0-23 (UTC)."
        if not 0 <= at_hour <= 23:
            return False, "at_hour must be between 0 and 23."
        schedule["at_hour"] = at_hour
    else:
        try:
            interval = int(schedule.get("interval_minutes", 60))
        except (TypeError, ValueError):
            return False, "interval_minutes must be a positive integer."
        if interval <= 0:
            return False, "interval_minutes must be a positive integer."
        schedule["interval_minutes"] = interval
    if schedule.get("type", "haiku") not in VALID_TYPES:
        return False, f"type must be one of: {', '.join(sorted(VALID_TYPES))}."
    if not str(schedule.get("prompt") or "").strip():
        return False, "Schedule must have a prompt."
    return True, ""

# Add email skill to path for direct import (stdlib-only, no sub-agent needed)
_EMAIL_SKILL_DIR = Path(__file__).parent.parent / "skills" / "email"
if str(_EMAIL_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_EMAIL_SKILL_DIR))


class Scheduler:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        # Exposed via the /health endpoint so an external watchdog can tell
        # a live heartbeat from a stalled one.
        self.last_heartbeat_at: datetime | None = None
        self.last_heartbeat_duration_s: float | None = None

    async def start(self):
        """Infinite loop: heartbeat → check schedules → sleep."""
        logger.info("Scheduler started")
        while True:
            pass_start = time.monotonic()
            try:
                await self._heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")

            try:
                await self._check_schedules()
            except Exception as e:
                logger.error(f"Schedule check error: {e}")

            duration = time.monotonic() - pass_start
            self.last_heartbeat_at = datetime.now(timezone.utc)
            self.last_heartbeat_duration_s = duration
            if duration > 60:
                logger.warning(f"Heartbeat pass took {duration:.0f}s — investigate what stalled")

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _heartbeat(self):
        """Periodic maintenance: prune, promote, check scratch."""
        logger.info("Heartbeat running")
        memory = self.orchestrator.memory

        await self._flush_startup_warnings()

        await memory.prune_short_term()
        # TODO(M2): remove the short_term→long_term Haiku promotion path
        # entirely. The knowledge layer (people/interactions/inbox_items) is
        # now the durable store; long_term.md is read-only legacy until then.
        # await memory.promote_to_long_term(self.orchestrator.haiku_query)
        # if not (memory.memory_dir / ".dedup_done").exists():
        #     await memory.deduplicate_long_term(self.orchestrator.haiku_query)
        #     (memory.memory_dir / ".dedup_done").touch()

        await self.orchestrator.check_scratch_pad()
        await self.orchestrator.reload_skills()
        await self._check_emails()
        await self._check_bookmark_digest()
        await self._sweep_wiki()

        logger.info("Heartbeat complete")

    async def _flush_startup_warnings(self):
        """Deliver queued startup problems (e.g. webhook bind failure) to the user."""
        warnings = self.orchestrator.startup_warnings
        if not warnings or not self.orchestrator.send_to_user:
            return
        pending, warnings[:] = warnings[:], []
        for message in pending:
            try:
                await self.orchestrator.send_to_user(f"⚠️ {message}")
            except Exception as e:
                logger.error(f"Failed to deliver startup warning: {e}")
                warnings.append(message)  # retry next heartbeat

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

            # One malformed file (e.g. at_hour as a string) must not abort
            # the whole pass and stop every other schedule from firing.
            try:
                due = self._is_due(schedule, now)
            except Exception as e:
                logger.warning(f"Bad schedule file {path.name}: {e}")
                continue

            if due:
                try:
                    self._mark_attempt(path, now)
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
        # last_attempt is set just before a run and cleared on success, so its
        # presence means the last run failed or was interrupted by a restart.
        # Back off instead of retrying (and re-notifying) every heartbeat.
        last_attempt_str = schedule.get("last_attempt")
        if last_attempt_str:
            last_attempt = datetime.fromisoformat(last_attempt_str)
            if (now - last_attempt).total_seconds() < RETRY_COOLDOWN_SEC:
                return False

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
        elif task_type == "internal":
            result = await self._run_internal(prompt)
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

    async def _sweep_wiki(self):
        """Assign any bookmarks not yet in a wiki topic (e.g. saved via sub-agent)."""
        try:
            from agent.knowledge import wiki as wiki_mod
            n = await wiki_mod.sweep_unassigned(limit=20)
            if n:
                logger.info(f"Wiki sweep assigned {n} new bookmark(s) to topics")
                # Refresh stale articles for affected topics
                await wiki_mod.drain_dirty(limit=10)
        except Exception as e:
            logger.warning(f"Wiki sweep failed: {e}")

    async def _run_internal(self, prompt: str) -> str:
        """Internal-type schedules: pure-Python jobs, no LLM call by the dispatcher."""
        if prompt == "__BACKUP__":
            try:
                from agent.backup import run_backup
                return await asyncio.to_thread(run_backup)
            except Exception as e:
                logger.exception("Backup failed")
                return f"Error: {e}"
        if prompt == "__WIKI_MAINTENANCE__":
            try:
                from agent.knowledge import wiki as wiki_mod
                stats = await wiki_mod.run_maintenance()
                return (
                    f"Wiki maintenance: {stats.get('topics', 0)} topics, "
                    f"{stats.get('bookmarks', 0)} bookmarks, "
                    f"{stats.get('deleted', 0)} deleted, "
                    f"{stats.get('resynthesized', 0)} articles refreshed."
                )
            except Exception as e:
                logger.exception("Wiki maintenance failed")
                return f"Error: {e}"
        return f"Error: unknown internal prompt {prompt!r}"

    async def _check_emails(self):
        """Poll for new unseen emails and notify user via Telegram."""
        try:
            import email_client
            from forward_parser import is_forwarded_from_milos
            new = await asyncio.wait_for(
                asyncio.to_thread(email_client.check_new_emails),
                timeout=EMAIL_CHECK_TIMEOUT,
            )
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
            data.pop("last_attempt", None)
            _write_schedule_json(schedule_path, data)
        except Exception as e:
            logger.error(f"Failed to update last_run for {schedule_path.name}: {e}")

    def _mark_attempt(self, schedule_path: Path, now: datetime):
        """Record that a run is starting; cleared by _mark_ran on success."""
        try:
            data = json.loads(schedule_path.read_text())
            data["last_attempt"] = now.isoformat()
            _write_schedule_json(schedule_path, data)
        except Exception as e:
            logger.error(f"Failed to update last_attempt for {schedule_path.name}: {e}")


def _write_schedule_json(path: Path, data: dict):
    """Atomic write (tmp + rename) so a crash can't truncate a schedule file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
