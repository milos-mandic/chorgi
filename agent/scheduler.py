"""Scheduler — heartbeat loop and scheduled task execution."""

import asyncio
import json
import logging
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
            if is_error:
                await self.orchestrator.send_to_user(f"⚠️ Scheduled task '{name}' failed:\n{result}")
            else:
                await self.orchestrator.send_to_user(f"[{name}]\n\n{result}")

        logger.info(f"Schedule {name} completed")

    async def _check_emails(self):
        """Poll for new unseen emails and notify user via Telegram."""
        try:
            import email_client
            new = await asyncio.to_thread(email_client.check_new_emails)
            if not new:
                return
            for e in new:
                sender = e.get("from", "unknown")
                subject = e.get("subject", "(no subject)")
                preview = e.get("body_preview", "")
                msg = f"\U0001f4e7 New email from {sender}\nSubject: {subject}\n{preview}"
                if self.orchestrator.send_to_user:
                    await self.orchestrator.send_to_user(msg)
            logger.info(f"Notified user of {len(new)} new email(s)")
        except Exception as e:
            logger.debug(f"Email check skipped: {e}")

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
