"""Haiku fast path — classification and quick responses in a single API call."""

import json
import re

from agent.api_client import call_haiku


async def classify_and_respond(
    message: str,
    history: list[dict],
    context: str,
    router_prompt: str,
) -> dict:
    """Single Haiku call: classify intent, optionally respond inline."""
    messages = []
    for turn in history[-5:]:
        messages.append(turn)
    messages.append({"role": "user", "content": message})

    raw, usage = await call_haiku(
        system=f"{router_prompt}\n\n# User Context\n{context}",
        messages=messages,
        max_tokens=512,
    )

    # Try to parse JSON, handling possible markdown fencing
    result = None
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        pass

    if result is None:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

    if result is None:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

    if result is None:
        result = {"route": "haiku", "response": raw}

    result["_usage"] = usage
    return result
