"""Step 3: micro_compact — placeholder old tool result bodies.

Walks history and replaces all but the last 5 tool-result bodies with a
fixed placeholder. Runs unconditionally — no threshold — because the cost
of the walk is negligible and the operation is idempotent once everything
old is already a placeholder.
"""
from __future__ import annotations

from typing import Callable

from ..core.messages import Message

MICRO_KEEP_RECENT_TOOL_RESULTS = 5
PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"

MetaSink = Callable[[str], None]


def run(*, history: list[Message], meta: MetaSink | None) -> int:
    """Replace old tool-result bodies with the placeholder. Returns # replaced."""
    tool_idxs = [i for i, m in enumerate(history) if m.role == "tool"]
    if len(tool_idxs) <= MICRO_KEEP_RECENT_TOOL_RESULTS:
        return 0
    cutoff = len(tool_idxs) - MICRO_KEEP_RECENT_TOOL_RESULTS
    replaced = 0
    for i in tool_idxs[:cutoff]:
        if history[i].content != PLACEHOLDER:
            history[i].content = PLACEHOLDER
            replaced += 1
    if meta and replaced:
        meta(f"[ctx: replaced {replaced} old tool result"
             f"{'s' if replaced > 1 else ''} with placeholder]")
    return replaced
