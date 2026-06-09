"""Stdlib-only Anthropic API client — no third-party dependencies."""

import asyncio
import json
import os
import random
import time
import urllib.error
import urllib.request


API_URL = "https://api.anthropic.com/v1/messages"

MAX_ATTEMPTS = 3
# Overloaded/rate-limit/server errors are transient; other 4xx are not.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


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

    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        retry_after = None
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
            return data["content"][0]["text"], data.get("usage", {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:500]
            if e.code not in _RETRYABLE_STATUS:
                raise RuntimeError(f"Anthropic API HTTP {e.code}: {detail}") from e
            last_error = RuntimeError(f"Anthropic API HTTP {e.code}: {detail}")
            retry_after = e.headers.get("retry-after") if e.headers else None
        except urllib.error.URLError as e:
            last_error = RuntimeError(f"Anthropic API connection error: {e.reason}")

        if attempt < MAX_ATTEMPTS - 1:
            delay = 2 ** attempt + random.uniform(0, 0.5)
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            time.sleep(delay)

    raise last_error


async def call_haiku(system: str, messages: list[dict], max_tokens: int = 512) -> tuple[str, dict]:
    """Async wrapper — runs the blocking HTTP call in a thread. Returns (text, usage)."""
    return await asyncio.to_thread(
        _call_messages_sync, system, messages, max_tokens, "claude-haiku-4-5-20251001"
    )
