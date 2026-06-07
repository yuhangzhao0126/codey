"""load_skill: fetch the body of one named skill from the SkillRegistry.

The system prompt's skills index already gives the model `name + description`
for every skill; this tool returns the full body of one on demand. The
registry is populated once at Session.build (no live reload), so this is
an O(1) in-memory read — no disk I/O, no permission gating.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..skills import SkillRegistry


@dataclass
class LoadSkillTool:
    skills: SkillRegistry = None  # type: ignore[assignment]

    name: str = "load_skill"
    description: str = (
        "Load the full body of a named skill. The skill index in your system "
        "prompt lists every available skill with a short description; this "
        "tool fetches the body of one when you decide to use it. Pass the "
        "skill name (matches the bullet in the index). Returns the skill's "
        "markdown body. Returns 'error: ...' if no such skill exists."
    )
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The skill name as shown in the index.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        name = (arguments.get("name") or "").strip()
        if not name:
            return "error: empty skill name"
        if self.skills is None:
            return "error: load_skill is not wired to a skill registry"
        skill = self.skills.get(name)
        if skill is None:
            available = ", ".join(self.skills.names()) or "(none)"
            return f"error: no skill named {name!r}. Available: {available}"
        return skill.body
