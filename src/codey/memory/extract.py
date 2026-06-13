"""Stop-hook extractor: propose memory candidates from the just-finished turn."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..core.messages import Message

_SYSTEM = (
    "You extract long-term memory entries from a conversation between a user "
    "and a coding agent. You will receive the most recent turn's messages. "
    "Output a JSON ARRAY (possibly empty) of candidate entries to remember "
    "across future sessions. Each candidate is an object: "
    '{"name":"snake_case_id","description":"one line","body":"longer text",'
    '"type":"preference|project|fact|style|other","scope":"global|project"}.\n\n'
    "GUIDELINES:\n"
    "- Save a candidate ONLY if the user explicitly stated a preference, "
    "convention, fact, or rule that should apply across future sessions.\n"
    "- If nothing is clearly worth saving, return [].\n"
    "- Prefer scope=global for cross-repo preferences, scope=project for "
    "things specific to this codebase.\n"
    "- Pick a short snake_case name that summarizes the rule.\n"
    "- The description is ONE line; the body can be 1-5 sentences with the "
    "rule plus brief context (when/why).\n"
    "- NEVER save secrets, API keys, passwords, tokens, file paths under "
    "/secrets, or anything that looks like credentials.\n"
    "Output ONLY the JSON array, nothing else."
)

_SECRET_PATTERNS = [
    re.compile(r"\bsk-[a-zA-Z0-9_\-]{6,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{8,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

_VALID_SCOPES = {"global", "project"}


@dataclass(frozen=True)
class MemoryCandidate:
    name: str
    description: str
    body: str
    type: str
    scope: str


def _looks_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def _render_turn(messages: Iterable[Message]) -> str:
    out: list[str] = []
    for m in messages:
        if m.role == "system":
            continue
        role = m.role
        content = (m.content or "").strip()
        if not content and not m.tool_calls:
            continue
        if m.tool_calls:
            calls = ", ".join(
                c.get("function", {}).get("name", "?") for c in m.tool_calls
            )
            out.append(f"[{role}] (tool calls: {calls}) {content}")
        else:
            out.append(f"[{role}] {content}")
    return "\n\n".join(out)[:16000]


async def propose_candidates(
    messages: Iterable[Message],
    *,
    existing_index: str,
    client: Any,
    model: str,
    max_candidates: int = 3,
) -> list[MemoryCandidate]:
    body = _render_turn(messages)
    if not body.strip():
        return []
    user_prompt = "## Existing memory index\n" + (existing_index or "(none)")
    user_prompt += "\n\n## Most recent turn\n" + body

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=800,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    out: list[MemoryCandidate] = []
    for entry in data[:max_candidates]:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        desc = (entry.get("description") or "").strip()
        body_text = (entry.get("body") or "").strip()
        type_ = (entry.get("type") or "other").strip() or "other"
        scope = (entry.get("scope") or "project").strip()
        if not name or not desc or not body_text:
            continue
        if scope not in _VALID_SCOPES:
            continue
        if _looks_secret(body_text) or _looks_secret(desc) or _looks_secret(name):
            continue
        out.append(MemoryCandidate(
            name=name, description=desc, body=body_text,
            type=type_, scope=scope,
        ))
    return out
