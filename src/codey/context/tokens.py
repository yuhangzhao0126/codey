"""Cheap chars/4 token estimator.

Used to decide when to trigger the LLM-summary step. The pipeline pads
its threshold with `compact_headroom` (default 13000 tokens) so per-model
tokenizer variance doesn't matter — being within 10-20% of the real count
is good enough.

No external deps. Deterministic. Pure function over a history list.
"""
from __future__ import annotations

from ..core.messages import Message


def estimate(history: list[Message]) -> int:
    """Estimate the prompt token count for a list of Message objects."""
    total_chars = 0
    for m in history:
        if m.content:
            total_chars += len(m.content)
        if m.tool_calls:
            for tc in m.tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name") or ""
                args = fn.get("arguments") or ""
                total_chars += len(name) + len(args)
        if m.name:
            total_chars += len(m.name)
    return total_chars // 4
