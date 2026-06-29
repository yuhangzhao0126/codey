"""CompactTool: model-callable trigger for proactive context summary.

Like spawn_agent, this tool needs the live Session because it acts on
the agent's history + client + meta_writer + recent_reads. It's
registered from Session.build AFTER the Session exists.

Pure in the contractual sense: no permission logic, no UI rendering,
returns a string. The orchestrator (Agent.run) interprets a single
`compact` tool call as a turn-ending signal so the next user prompt
sees the compacted context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .. import context as context_pipeline

if TYPE_CHECKING:
    from ..core.session import Session


@dataclass
class CompactTool:
    session_provider: Callable[[], "Session"] | None = None

    name: str = "compact"
    description: str = (
        "Force compact the conversation history NOW. Summarizes prior turns "
        "into a short message and re-injects the most recent files you read. "
        "After this returns, the current turn ends; you'll see the compacted "
        "context on the next user message. Call this when the conversation "
        "is long and you want a clean slate while preserving what you've "
        "learned. Call this tool ALONE in your turn — do not mix with other "
        "tool calls."
    )
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    })

    async def run(self, arguments: dict[str, Any]) -> str:
        sess = self.session_provider() if self.session_provider else None
        if sess is None:
            return "error: compact tool is not wired to a session"
        agent = sess.agent
        try:
            await context_pipeline.run_proactive_force_summary(
                history=agent.history,
                provider=agent.provider,
                session_id=agent.session_id,
                meta=agent._meta,
                client=agent._client,
                recent_files=list(agent._recent_reads),
            )
        except Exception as e:  # noqa: BLE001
            return f"error: compact failed: {type(e).__name__}: {e}"
        if agent._meta:
            agent._meta("[ctx: forced compaction by model]")
        return "[Compacted. History summarized.]"
