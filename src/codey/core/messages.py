"""Wire-format chat messages.

Lifted out of agent.py so the schema lives in one place independent of the
turn loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str = ""
    # Assistant turns that requested tool use carry tool_calls.
    tool_calls: list[dict[str, Any]] | None = None
    # Tool result turns reference the originating call.
    tool_call_id: str | None = None
    name: str | None = None  # for tool messages: tool name

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the OpenAI chat-completions wire format."""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg
