"""Stdlib-only Anthropic API client — no third-party dependencies."""

import asyncio
import json
import os
import urllib.error
import urllib.request


API_URL = "https://api.anthropic.com/v1/messages"


def _call_messages_sync(system: str, messages: list[dict], max_tokens: int, model: str) -> tuple[str, dict]:
    """Synchronous HTTP call to Anthropic Messages API. Returns (text, usage)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system
    body = json.dumps(payload).encode()

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API HTTP {e.code}: {e.read().decode()[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Anthropic API connection error: {e.reason}") from e

    return data["content"][0]["text"], data.get("usage", {})


async def call_haiku(system: str, messages: list[dict], max_tokens: int = 512) -> tuple[str, dict]:
    """Async wrapper — runs the blocking HTTP call in a thread. Returns (text, usage)."""
    return await asyncio.to_thread(
        _call_messages_sync, system, messages, max_tokens, "claude-haiku-4-5-20251001"
    )
