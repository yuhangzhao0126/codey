"""load_memory: fetch one memory entry's body by name.

Mirrors load_skill — the memory index in the system prompt lists every
available entry with its description; this tool returns the full body of
one on demand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory import MemoryRegistry


@dataclass
class LoadMemoryTool:
    registry: MemoryRegistry = None  # type: ignore[assignment]
    name: str = "load_memory"
    description: str = (
        "Load the full body of a named memory entry. The memory index in "
        "your system prompt lists every entry with a short description; "
        "this tool fetches the body of one when you decide it is relevant. "
        "Pass the memory name (matches the bullet in the index). Returns "
        "the memory markdown body. Returns 'error: ...' if no such entry exists."
    )
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Entry name as shown in the index."},
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        name = (arguments.get("name") or "").strip()
        if not name:
            return "error: empty memory name"
        if self.registry is None:
            return "error: load_memory not wired to a registry"
        mem = self.registry.get(name)
        if mem is None:
            available = ", ".join(self.registry.names()) or "(none)"
            return f"error: no memory named {name!r}. Available: {available}"
        return mem.body
