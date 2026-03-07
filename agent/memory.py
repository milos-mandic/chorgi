"""Memory management — context assembly, read/write, pruning, promotion."""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SHORT_TERM_LINES = 100
HAIKU_CONTEXT_TAIL = 20


class Memory:
    def __init__(self, personal_dir: Path):
        self.personal_dir = personal_dir
        self.memory_dir = personal_dir / "memory"

    def _read_file(self, path: Path) -> str:
        try:
            if path.exists():
                return path.read_text()
        except OSError as e:
            logger.warning(f"Failed to read {path}: {e}")
        return ""

    # --- Context assembly ---

    def get_haiku_context(self) -> str:
        """Lightweight context for Haiku classification: identity + context + recent short-term."""
        parts = []
        for name in ("identity.md", "context.md"):
            text = self._read_file(self.personal_dir / name)
            if text:
                parts.append(text)

        short = self._read_file(self.memory_dir / "short_term.md")
        if short:
            tail = "\n".join(short.splitlines()[-HAIKU_CONTEXT_TAIL:])
            parts.append(f"# Recent Memory\n{tail}")

        return "\n\n".join(parts)

    def get_full_context(self) -> str:
        """Full context for sub-agents: identity + context + long-term + short-term."""
        parts = []
        for name in ("identity.md", "context.md"):
            text = self._read_file(self.personal_dir / name)
            if text:
                parts.append(text)

        long = self._read_file(self.memory_dir / "long_term.md")
        if long:
            parts.append(f"# Long-Term Memory\n{long}")

        short = self._read_file(self.memory_dir / "short_term.md")
        if short:
            parts.append(f"# Short-Term Memory\n{short}")

        return "\n\n".join(parts)

    # --- Write operations ---

    def append_short_term(self, entry: str):
        """Append a timestamped entry to short_term.md."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"- [{ts}] {entry}\n"
        path = self.memory_dir / "short_term.md"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a") as f:
                f.write(line)
        except OSError as e:
            logger.warning(f"Failed to append to short_term.md: {e}")

    async def prune_short_term(self):
        """Keep only the last N lines of short_term.md."""
        path = self.memory_dir / "short_term.md"
        text = self._read_file(path)
        if not text:
            return
        lines = text.splitlines()
        if len(lines) <= MAX_SHORT_TERM_LINES:
            return
        pruned = "\n".join(lines[-MAX_SHORT_TERM_LINES:]) + "\n"
        try:
            path.write_text(pruned)
        except OSError as e:
            logger.warning(f"Failed to prune short_term.md: {e}")
            return
        logger.info(f"Pruned short_term.md from {len(lines)} to {MAX_SHORT_TERM_LINES} lines")

    async def promote_to_long_term(self, haiku_fn):
        """Use Haiku to identify durable facts in short_term and move them to long_term."""
        path = self.memory_dir / "short_term.md"
        short = self._read_file(path)
        if not short or len(short.strip().splitlines()) < 5:
            return  # Not enough to evaluate

        prompt = (
            "Review these short-term memory entries and identify any durable facts "
            "worth keeping permanently (e.g. user preferences, project decisions, "
            "important outcomes). Return ONLY a JSON object with two keys:\n"
            '- "promote": list of strings to add to long-term memory\n'
            '- "remove_lines": list of exact lines to remove from short-term\n'
            "If nothing is worth promoting, return empty lists.\n\n"
            f"Entries:\n{short}"
        )

        try:
            result = await haiku_fn(prompt)
        except Exception as e:
            logger.error(f"Promotion Haiku call failed: {e}")
            return

        # Parse response
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", result, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning("Could not parse promotion response")
                    return
            else:
                return

        to_promote = data.get("promote", [])
        to_remove = set(data.get("remove_lines", []))

        if to_promote:
            lt_path = self.memory_dir / "long_term.md"
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                with open(lt_path, "a") as f:
                    for item in to_promote:
                        f.write(f"- [{ts}] {item}\n")
                logger.info(f"Promoted {len(to_promote)} entries to long_term.md")
            except OSError as e:
                logger.warning(f"Failed to write long_term.md: {e}")

        if to_remove:
            remaining = [l for l in short.splitlines() if l.strip() not in to_remove]
            try:
                path.write_text("\n".join(remaining) + "\n" if remaining else "")
                logger.info(f"Removed {len(to_remove)} promoted lines from short_term.md")
            except OSError as e:
                logger.warning(f"Failed to rewrite short_term.md: {e}")

    # --- Scratch pad ---

    def read_scratch(self) -> str:
        return self._read_file(self.memory_dir / "scratch.md")

    def clear_scratch(self):
        path = self.memory_dir / "scratch.md"
        if path.exists():
            try:
                path.write_text("")
            except OSError as e:
                logger.warning(f"Failed to clear scratch.md: {e}")
