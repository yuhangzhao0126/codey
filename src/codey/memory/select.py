"""Turn-start side-query: pick up to k relevant memory names.

Two layers:
  1. pick_relevant — one LLM call returning a JSON list of names.
  2. pick_relevant_keyword — fallback: token overlap on name+description.

The LLM is prompted to bias toward [] when nothing is clearly relevant.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .registry import MemoryRegistry

_TOKEN_RE = re.compile(r"[a-z0-9]+")
DEFAULT_K = 5
_KEYWORD_MIN_SCORE = 1

_SYSTEM = (
    "You select which long-term memory entries are relevant to the user's "
    "current message. You will receive an index of (name, description) "
    "bullets and the user's most recent message. Return a JSON ARRAY of "
    "AT MOST {k} entry names that are CLEARLY relevant. If nothing is "
    "clearly relevant, return []. Do not include uncertain matches. "
    "Output ONLY the JSON array, nothing else."
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def pick_relevant_keyword(
    user_text: str, registry: MemoryRegistry, *, k: int = DEFAULT_K,
) -> list[str]:
    toks = _tokens(user_text)
    if not toks:
        return []
    scored: list[tuple[int, str]] = []
    for name in registry.names():
        m = registry.get(name)
        if m is None:
            continue
        entry_tokens = _tokens(f"{name} {m.description}")
        score = len(toks & entry_tokens)
        if score >= _KEYWORD_MIN_SCORE:
            scored.append((score, name))
    scored.sort(key=lambda p: (-p[0], p[1]))
    return [name for _, name in scored[:k]]


async def pick_relevant(
    user_text: str,
    registry: MemoryRegistry,
    *,
    client: Any,
    model: str,
    k: int = DEFAULT_K,
    last_assistant: str = "",
) -> list[str]:
    """One LLM call → list of names. Fallback to keyword scoring on any error."""
    if not registry.names():
        return []
    index = registry.list_meta().strip()
    if not index:
        return []
    user_prompt = (
        "## Index\n" + index + "\n\n"
        "## Recent user message\n" + (user_text or "").strip()[:4000]
    )
    if last_assistant:
        user_prompt += "\n\n## Last assistant message\n" + last_assistant.strip()[:1000]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM.format(k=k)},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        names = json.loads(text)
        if not isinstance(names, list):
            raise ValueError("not a list")
        valid = [n for n in names if isinstance(n, str) and registry.get(n) is not None]
        return valid[:k]
    except Exception:
        return pick_relevant_keyword(user_text, registry, k=k)
