"""Events emitted by Agent.run().

UIs consume these; the agent owns the orchestration. Lifted out of agent.py
so the event vocabulary is one file you can scan.

Event flow for a single user turn:

    TurnStarted
      [ for each model round: ]
        RoundStarted(round)
        AssistantTextDelta(text)*         # streamed text
        ToolCallRequested(id, name, args) # once args fully parsed
        ToolResult(id, name, ok, content) # after local dispatch
      AssistantMessageCompleted(text)     # text concatenated across rounds
    TurnCompleted(reason, error?)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class TurnStarted:
    pass


@dataclass
class RoundStarted:
    round: int  # 0-indexed


@dataclass
class AssistantTextDelta:
    text: str


@dataclass
class AssistantMessageCompleted:
    text: str  # full assistant text for the turn (concatenated across rounds)


@dataclass
class ToolCallRequested:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    id: str
    name: str
    ok: bool
    content: str


@dataclass
class TurnCompleted:
    reason: Literal["stop", "error", "cancelled"]
    error: str | None = None


Event = (
    TurnStarted
    | RoundStarted
    | AssistantTextDelta
    | AssistantMessageCompleted
    | ToolCallRequested
    | ToolResult
    | TurnCompleted
)
