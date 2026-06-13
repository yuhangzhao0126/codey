"""remember_this: save a new memory entry inline mid-turn.

Parent-only tool — children do not see it (the parent owns the long-term
store, same way it owns the todo list and the compact command).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..memory import MemoryRegistry, MemoryStore
from ..memory.models import Memory

_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_VALID_SCOPES = {"global", "project"}


@dataclass
class RememberThisTool:
    registry: MemoryRegistry = None  # type: ignore[assignment]
    store: MemoryStore = None         # type: ignore[assignment]
    session_id: str = ""
    default_scope: str = "project"
    name: str = "remember_this"
    description: str = (
        "Save a new long-term memory entry. Use this when the user has "
        "stated a preference, project convention, or fact you should "
        "recall in future sessions. Choose a short snake_case name that "
        "summarizes the rule (e.g. 'use_pnpm'). The description should "
        "be one line; the body can be longer. Set scope='global' for "
        "cross-repo preferences, 'project' for things specific to this "
        "repo. Returns 'ok: <path>' or 'error: ...'."
    )
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "name":        {"type": "string"},
                "description": {"type": "string"},
                "body":        {"type": "string"},
                "type":        {"type": "string",
                                "description": "preference | project | fact | style | other"},
                "scope":       {"type": "string", "enum": ["global", "project"]},
            },
            "required": ["name", "description", "body"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        name = (arguments.get("name") or "").strip()
        desc = (arguments.get("description") or "").strip()
        body = (arguments.get("body") or "").strip()
        type_ = (arguments.get("type") or "other").strip() or "other"
        scope = (arguments.get("scope") or self.default_scope).strip()

        if not _NAME_RE.match(name):
            return "error: name must be snake_case, 1-64 chars, [a-z0-9_]"
        if not desc:
            return "error: description is required"
        if not body:
            return "error: body is required"
        if scope not in _VALID_SCOPES:
            return f"error: scope must be one of {sorted(_VALID_SCOPES)}"
        if self.store is None or self.registry is None:
            return "error: remember_this not wired"

        now = datetime.now().isoformat(timespec="seconds")
        existing = self.registry.get(name)
        created_at = existing.created_at if existing is not None else now
        scope_lit = "global" if scope == "global" else "project"
        m = Memory(
            name=name, description=desc, type=type_, body=body,
            created_at=created_at, updated_at=now,
            source_session=self.session_id, scope=scope_lit,
            source_path=Path("/placeholder"),
        )
        try:
            path = await self.store.write(m, scope=scope_lit, source="tool")
        except Exception as e:  # noqa: BLE001
            return f"error: write failed: {type(e).__name__}: {e}"

        from ..memory.io import parse_memory_md
        parsed = parse_memory_md(
            path.read_text(encoding="utf-8"),
            filename_stem=name, source_path=path, scope=scope_lit,
        )
        if not isinstance(parsed, str):
            self.registry._memories[name] = parsed
        return f"ok: {path}"
