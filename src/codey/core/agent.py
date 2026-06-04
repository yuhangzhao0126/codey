"""Tool protocol + registry.

The actual tool implementations live in src/codey/tools/. This file defines
the interface the Agent loop calls through and the registry that holds the
mapping from name → tool instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema

    async def run(self, arguments: dict[str, Any]) -> str: ...


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI tool-spec list. Empty list disables tool use."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.tools.values()
        ]

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        tool = self.tools.get(name)
        if tool is None:
            return False, f"unknown tool: {name}"
        try:
            return True, await tool.run(arguments)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
