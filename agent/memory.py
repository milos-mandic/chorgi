"""Memory management — context assembly, read/write, pruning, promotion."""

import hashlib
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
        self._last_promote_hash: str | None = None

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

        content_hash = hashlib.md5(short.encode()).hexdigest()
        if content_hash == self._last_promote_hash:
            return  # Nothing changed since last promotion check

        lt_path = self.memory_dir / "long_term.md"
        existing_long = self._read_file(lt_path)

        existing_section = ""
        if existing_long.strip():
            existing_section = (
                "\n\nExisting long-term memory (DO NOT promote duplicates of these):\n"
                f"{existing_long}"
            )

        prompt = (
            "Review these short-term memory entries and identify any durable facts "
            "worth keeping permanently (e.g. user preferences, project decisions, "
            "important outcomes). Return ONLY a JSON object with three keys:\n"
            '- "promote": list of strings to add to long-term memory\n'
            '- "remove_lines": list of exact lines to remove from short-term\n'
            '- "remove_long_term": list of exact lines from existing long-term memory '
            "that are superseded by new promotions (empty if none)\n"
            "Do NOT promote anything already covered by existing long-term memory. "
            "If a new entry updates an existing fact, put the old line in "
            '"remove_long_term" and the updated version in "promote".\n'
            "If nothing is worth promoting, return empty lists.\n\n"
            f"Short-term entries:\n{short}{existing_section}"
        )

        try:
            result = await haiku_fn(prompt)
        except Exception as e:
            logger.error(f"Promotion Haiku call failed: {e}")
            return

        self._last_promote_hash = content_hash

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
        to_remove_lt = set(data.get("remove_long_term", []))

        # Remove superseded long-term entries before appending new ones
        if to_remove_lt:
            lt_content = self._read_file(lt_path)
            if lt_content:
                remaining_lt = [l for l in lt_content.splitlines() if l.strip() not in to_remove_lt]
                try:
                    lt_path.write_text("\n".join(remaining_lt) + "\n" if remaining_lt else "")
                    logger.info(f"Removed {len(to_remove_lt)} superseded lines from long_term.md")
                except OSError as e:
                    logger.warning(f"Failed to rewrite long_term.md: {e}")

        if to_promote:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                with open(lt_path, "a") as f:
                    for item in to_promote:
                        f.write(f"- [{ts}] {item}\n")
                logger.info(f"Promoted {len(to_promote)} entries to long_term.md")
            except OSError as e:
                logger.warning(f"Failed to write long_term.md: {e}")

        if to_remove:
            # Re-read to avoid losing entries appended during the Haiku await
            current_short = self._read_file(path)
            remaining = [l for l in current_short.splitlines() if l.strip() not in to_remove]
            try:
                path.write_text("\n".join(remaining) + "\n" if remaining else "")
                logger.info(f"Removed {len(to_remove)} promoted lines from short_term.md")
            except OSError as e:
                logger.warning(f"Failed to rewrite short_term.md: {e}")

    async def deduplicate_long_term(self, haiku_fn):
        """One-time dedup: send full long_term.md to Haiku, replace with clean list."""
        lt_path = self.memory_dir / "long_term.md"
        content = self._read_file(lt_path)
        if not content or len(content.strip().splitlines()) < 3:
            return

        line_count = len(content.strip().splitlines())
        prompt = (
            "Below is a long-term memory file that has accumulated duplicates. "
            "Deduplicate it: merge redundant entries, keep the most complete/recent "
            "version of each fact, and remove anything trivial or outdated.\n"
            'Return ONLY a JSON object with one key: "keep" — a list of strings, '
            "each a clean memory entry (no timestamps, no bullet markers).\n"
            "Preserve ALL unique facts — do not discard anything that isn't a duplicate.\n\n"
            f"Long-term memory:\n{content}"
        )

        try:
            result = await haiku_fn(prompt)
        except Exception as e:
            logger.error(f"Dedup Haiku call failed: {e}")
            return

        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", result, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning("Could not parse dedup response")
                    return
            else:
                return

        keep = data.get("keep", [])
        if not keep:
            logger.warning("Dedup returned empty keep list — skipping write")
            return

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            with open(lt_path, "w") as f:
                for item in keep:
                    f.write(f"- [{ts}] {item}\n")
            logger.info(f"Deduplicated long_term.md: {line_count} -> {len(keep)} entries")
        except OSError as e:
            logger.warning(f"Failed to write deduplicated long_term.md: {e}")

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
