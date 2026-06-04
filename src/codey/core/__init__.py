"""Core agent runtime.

Public re-exports for the agent's turn loop, message schema, and event vocabulary.
"""

from __future__ import annotations

from .agent import Tool, ToolRegistry
from .events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    Event,
    RoundStarted,
    ToolCallRequested,
    ToolResult,
    TurnCompleted,
    TurnStarted,
)
from .messages import Message, Role
from .turn import MAX_ROUNDS, Agent

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
]
