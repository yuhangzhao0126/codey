"""Back-compat shim for `from codey.agent import …`.

The canonical homes after the structural refactor:
  Agent, MAX_ROUNDS, _RoundDone     →  codey.core.turn
  Tool, ToolRegistry                →  codey.core.agent
  Message, Role                     →  codey.core.messages
  TurnStarted, RoundStarted, …, Event   →  codey.core.events

This module re-exports all of them so existing import sites keep working.
Removed in step 10 of the refactor; until then, prefer importing from the
canonical homes for new code.
"""

from __future__ import annotations

from .core.agent import Tool, ToolRegistry
from .core.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    Event,
    RoundStarted,
    ToolCallRequested,
    ToolResult,
    TurnCompleted,
    TurnStarted,
)
from .core.messages import Message, Role
from .core.turn import MAX_ROUNDS, Agent, _RoundDone

__all__ = [
    "Agent",
    "AssistantMessageCompleted",
    "AssistantTextDelta",
    "Event",
    "MAX_ROUNDS",
    "Message",
    "Role",
    "RoundStarted",
    "Tool",
    "ToolCallRequested",
    "ToolRegistry",
    "ToolResult",
    "TurnCompleted",
    "TurnStarted",
    "_RoundDone",
]
