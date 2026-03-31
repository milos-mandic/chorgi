"""Spawns Claude Code sub-agent processes for deep work."""

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolve claude binary at import time so launchd's minimal PATH doesn't matter
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")


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

    cmd = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "json",
        "--model", skill_config.get("model", "sonnet"),
        "--permission-mode", "bypassPermissions",
        "--allow-dangerously-skip-permissions",
    ]

    # MCP server config — provides typed tools instead of generic Bash access
    mcp_server = skill_config.get("mcp_server")
    if mcp_server:
        abs_args = []
        for a in mcp_server.get("args", []):
            candidate = Path(skill_dir) / a
            abs_args.append(str(candidate) if candidate.exists() else a)
        mcp_config = json.dumps({"mcpServers": {
            skill_name: {
                "type": "stdio",
                "command": mcp_server["command"],
                "args": abs_args,
            }
        }})
        cmd.extend(["--mcp-config", mcp_config])

    # Only add --allowedTools if there are non-MCP tools to allow
    tools = skill_config.get("tools", [])
    if tools:
        cmd.extend(["--allowedTools", ",".join(tools)])

    cmd.extend(["--max-turns", str(skill_config.get("max_turns", 10))])

    prompt = f"""# User Context
{context}

# Task
{task}

# Response Guidelines
- Be concise and conversational, like a helpful human assistant
- Lead with the result — what happened, what was done
- NEVER mention error codes, CLI flags, API names, file paths, or stack traces
- If something partially failed, explain what worked and what didn't in plain language
- Use short sentences. No filler.
- NEVER use markdown formatting (no **, *, `, #, ```, - for lists, etc.) — your output goes to Telegram which does not render markdown.
- Use plain text with emojis for emphasis. Use • for bullet points."""

    # Strip CLAUDECODE env var so the child doesn't think it's nested.
    # Strip ANTHROPIC_API_KEY so Claude Code uses OAuth instead of billing
    # against the API key (which would charge per-token for Opus).
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "ANTHROPIC_API_KEY")}

    logger.info(f"Spawning sub-agent for skill '{skill_name}': {task[:100]}")
    start = time.monotonic()
    process = None
    stdout = b""

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
            err_msg = stderr.decode().strip()
            out_msg = stdout.decode().strip()
            detail = err_msg or out_msg or f"Sub-agent exited with code {process.returncode}"
            logger.error(
                f"Sub-agent '{skill_name}' failed (rc={process.returncode}) in {elapsed:.1f}s. "
                f"stderr={err_msg!r} stdout={out_msg[:500]!r}"
            )
            return {"error": True, "message": detail, "elapsed_s": round(elapsed, 1)}

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
        if process is not None:
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
