"""Targeted consolidation: decide what to do with a single candidate.

Reads existing entries of the same `type` from the registry, asks the
LLM for one of:
  DUPLICATE   — skip; existing entry covers it
  UPDATE      — replace the body of a named existing entry
  SUPERSEDE   — delete one or more named entries, then write the candidate
  NOVEL       — write the candidate as-is

On any LLM error we bias toward NOVEL — never lose user-stated preferences.
"""
from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .extract import MemoryCandidate
from .models import Memory
from .registry import MemoryRegistry
from .store import MemoryStore

_SYSTEM = (
    "You are a memory consolidator. You will receive a CANDIDATE memory "
    "entry and a list of EXISTING entries of the same type. Decide one of:\n"
    '{"verdict":"DUPLICATE"}                — existing entries already cover it\n'
    '{"verdict":"UPDATE","target":"<name>"} — overwrite the named existing\n'
    "                                         entry with the candidate's body\n"
    '{"verdict":"SUPERSEDE","drop":["<n1>","<n2>"]}\n'
    "                                       — delete listed entries, then write candidate\n"
    '{"verdict":"NOVEL"}                    — candidate is new and unique; write as-is\n'
    "Output ONLY the JSON object."
)


class Decision(str, Enum):
    DUPLICATE = "DUPLICATE"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    NOVEL = "NOVEL"


def _to_memory(c: MemoryCandidate, *, session_id: str) -> Memory:
    now = datetime.now().isoformat(timespec="seconds")
    return Memory(
        name=c.name, description=c.description, type=c.type, body=c.body,
        created_at=now, updated_at=now, source_session=session_id,
        scope="global" if c.scope == "global" else "project",
        source_path=Path("/placeholder"),
    )


async def targeted_consolidate(
    candidate: MemoryCandidate,
    *,
    registry: MemoryRegistry,
    store: MemoryStore,
    session_id: str,
    client: Any,
    model: str,
) -> Decision:
    same_type = [m for m in registry.all() if m.type == candidate.type]
    existing_view = "\n\n".join(
        f"### {m.name} (scope={m.scope})\n{m.description}\n---\n{m.body[:400]}"
        for m in same_type
    ) or "(none)"
    candidate_view = (
        f"### {candidate.name} (scope={candidate.scope})\n"
        f"{candidate.description}\n---\n{candidate.body}"
    )
    user_prompt = (
        "## Existing entries (same type)\n" + existing_view
        + "\n\n## Candidate\n" + candidate_view
    )

    verdict = Decision.NOVEL
    target: str | None = None
    drop: list[str] = []
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(text)
        v = (data.get("verdict") or "").strip().upper()
        if v in {x.value for x in Decision}:
            verdict = Decision(v)
            target = data.get("target")
            drop = data.get("drop") or []
    except Exception:
        verdict = Decision.NOVEL

    if verdict == Decision.DUPLICATE:
        return verdict

    if verdict == Decision.UPDATE and target and registry.get(target):
        old = registry.get(target)
        if old is not None:
            now = datetime.now().isoformat(timespec="seconds")
            updated = Memory(
                name=old.name, description=old.description, type=old.type,
                body=candidate.body, created_at=old.created_at,
                updated_at=now, source_session=session_id, scope=old.scope,
                source_path=old.source_path,
            )
            path = await store.write(updated, scope=old.scope, source="extract")
            from .io import parse_memory_md
            parsed = parse_memory_md(
                path.read_text(encoding="utf-8"),
                filename_stem=old.name, source_path=path, scope=old.scope,
            )
            if not isinstance(parsed, str):
                registry._memories[old.name] = parsed
            return Decision.UPDATE

    if verdict == Decision.SUPERSEDE:
        for n in drop:
            existing = registry.get(n)
            if existing is None:
                continue
            await store.delete(n, scope=existing.scope, source="extract")
            registry._memories.pop(n, None)

    m = _to_memory(candidate, session_id=session_id)
    path = await store.write(m, scope=m.scope, source="extract")
    from .io import parse_memory_md
    parsed = parse_memory_md(
        path.read_text(encoding="utf-8"),
        filename_stem=m.name, source_path=path, scope=m.scope,
    )
    if not isinstance(parsed, str):
        registry._memories[m.name] = parsed
    return verdict if verdict == Decision.SUPERSEDE else Decision.NOVEL
