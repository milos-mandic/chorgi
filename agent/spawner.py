"""Spawns Claude Code sub-agent processes for deep work."""

import asyncio
import json
import logging
import os
import time

logger = logging.getLogger(__name__)


async def spawn_sub_agent(
    skill_config: dict,
    task: str,
    context: str,
) -> dict:
    """Spawn a Claude Code process for a specific skill.

    Claude Code reads CLAUDE.md automatically from --cwd.
    We pass the dynamic context and task via stdin.
    """
    skill_dir = skill_config["dir"]
    skill_name = skill_config.get("name", "unknown")

    # Ensure /tmp exists for Claude Code's sandbox (Termux doesn't have one natively,
    # but proot creates a real /tmp dir on /data that persists across processes)
    os.makedirs("/tmp", exist_ok=True)

    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--allowedTools", ",".join(skill_config["tools"]),
        "--max-turns", str(skill_config.get("max_turns", 10)),
    ]

    prompt = f"""# User Context
{context}

# Task
{task}

Complete the task. Be thorough but concise in your output."""

    # Strip CLAUDECODE env var so the child doesn't think it's nested
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    logger.info(f"Spawning sub-agent for skill '{skill_name}': {task[:100]}")
    start = time.monotonic()

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=skill_dir,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=prompt.encode()),
            timeout=skill_config.get("timeout_seconds", 120),
        )
        elapsed = time.monotonic() - start

        if process.returncode != 0:
            logger.error(f"Sub-agent '{skill_name}' failed (rc={process.returncode}) in {elapsed:.1f}s")
            return {"error": True, "message": stderr.decode().strip(), "elapsed_s": round(elapsed, 1)}

        result = json.loads(stdout.decode())
        # Extract the text result from Claude's JSON output
        if isinstance(result, dict) and "result" in result:
            text = result["result"]
        elif isinstance(result, list):
            # Multiple content blocks — concatenate text
            texts = [b.get("text", "") for b in result if b.get("type") == "text"]
            text = "\n".join(texts)
        else:
            text = str(result)

        logger.info(f"Sub-agent '{skill_name}' completed in {elapsed:.1f}s")
        return {"text": text, "elapsed_s": round(elapsed, 1)}

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        logger.error(f"Sub-agent '{skill_name}' timed out after {elapsed:.1f}s")
        process.kill()
        await process.wait()
        return {"error": True, "message": "Sub-agent timed out", "elapsed_s": round(elapsed, 1)}
    except json.JSONDecodeError:
        elapsed = time.monotonic() - start
        logger.info(f"Sub-agent '{skill_name}' returned non-JSON in {elapsed:.1f}s")
        # Non-JSON output — return raw text
        return {"text": stdout.decode().strip(), "elapsed_s": round(elapsed, 1)}
    except Exception as e:
        elapsed = time.monotonic() - start
        logger.error(f"Sub-agent '{skill_name}' error after {elapsed:.1f}s: {e}")
        return {"error": True, "message": str(e), "elapsed_s": round(elapsed, 1)}
