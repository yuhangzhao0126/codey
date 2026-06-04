"""Consume the Agent.run() event stream and render it via the renderers.

The CodeyApp owns the in-progress assistant buffer; this module reads/writes
it (passed in) so the streaming logic isn't tangled with worker management
in app.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    RoundStarted,
    ToolCallRequested,
    ToolResult,
    TurnCompleted,
    TurnStarted,
)
from .renderers import log_assistant, log_error, log_meta

if TYPE_CHECKING:
    from .app import CodeyApp


async def stream_turn(app: "CodeyApp", user_input: str) -> None:
    """Drive one user turn through the agent, batching text deltas and
    flushing on tool calls / completion so the transcript reads naturally."""
    app._assistant_buf = ""
    async for ev in app.agent.run(user_input):
        if isinstance(ev, (TurnStarted, RoundStarted)):
            pass
        elif isinstance(ev, AssistantTextDelta):
            app._assistant_buf += ev.text
        elif isinstance(ev, ToolCallRequested):
            # Flush any accumulated assistant text before the tool call
            # happens. Tool calls are invisible in the TUI by design (see
            # PR-C); the audit log records them.
            if app._assistant_buf.strip():
                log_assistant(app.transcript, app._assistant_buf.strip())
                app._assistant_buf = ""
        elif isinstance(ev, ToolResult):
            pass
        elif isinstance(ev, AssistantMessageCompleted):
            app._assistant_buf = ev.text
        elif isinstance(ev, TurnCompleted):
            if app._assistant_buf.strip():
                log_assistant(app.transcript, app._assistant_buf.strip())
            app._assistant_buf = ""
            if ev.reason == "error":
                log_error(app.transcript, ev.error or "unknown error")
            elif ev.reason == "cancelled":
                log_meta(app.transcript, "(cancelled)")
