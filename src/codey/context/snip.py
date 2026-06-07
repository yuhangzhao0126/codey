"""Step 2: snip_compact — trim the middle of long conversations.

When the non-system history exceeds 50 messages, keep the first 5 and last
45 and drop everything between. Replace the dropped block with a single
synthetic user marker so the model is aware of the gap.

Pair preservation: if either cut boundary falls inside a tool_call ↔
tool_result group, expand the keep-window outward so no orphan messages
survive. The two expander helpers are also re-used by reactive.py.
"""
from __future__ import annotations

from typing import Callable

from ..core.messages import Message

SNIP_THRESHOLD_MESSAGES = 50
SNIP_KEEP_HEAD = 5
SNIP_KEEP_TAIL = 45

MetaSink = Callable[[str], None]


def _expand_prefix_to_pair_boundary(body: list[Message], end_idx: int) -> int:
    """If body[end_idx-1] is an assistant.tool_calls, walk forward absorbing
    matching role:"tool" messages. Returns the new exclusive end index."""
    if end_idx <= 0 or end_idx > len(body):
        return end_idx
    last = body[end_idx - 1]
    if last.role != "assistant" or not last.tool_calls:
        return end_idx
    expected = {c["id"] for c in last.tool_calls if c.get("id")}
    if not expected:
        return end_idx
    i = end_idx
    seen: set[str] = set()
    while i < len(body) and body[i].role == "tool":
        if body[i].tool_call_id:
            seen.add(body[i].tool_call_id)
        i += 1
        if expected.issubset(seen):
            break
    return i


def _expand_suffix_to_pair_boundary(body: list[Message], start_idx: int) -> int:
    """If body[start_idx] is role:"tool", walk backward to include the
    originating assistant.tool_calls message + any sibling tool results
    that share its call group. Returns the new (possibly smaller) start."""
    if start_idx < 0 or start_idx >= len(body):
        return start_idx
    if body[start_idx].role != "tool":
        return start_idx
    i = start_idx
    while i > 0 and body[i - 1].role == "tool":
        i -= 1
    if i - 1 >= 0:
        prev = body[i - 1]
        if prev.role == "assistant" and prev.tool_calls:
            return i - 1
    return i


def run(*, history: list[Message], meta: MetaSink | None) -> int:
    """Trim the middle of history. Returns the number of messages dropped."""
    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    if len(body) <= SNIP_THRESHOLD_MESSAGES:
        return 0

    prefix_end = _expand_prefix_to_pair_boundary(body, SNIP_KEEP_HEAD)
    suffix_start = _expand_suffix_to_pair_boundary(
        body, len(body) - SNIP_KEEP_TAIL
    )
    if suffix_start <= prefix_end:
        return 0

    dropped = suffix_start - prefix_end
    if dropped <= 0:
        return 0

    marker = Message(
        role="user",
        content=f"[... {dropped} earlier message"
                f"{'s' if dropped > 1 else ''} compacted by snip ...]",
    )
    new_body = body[:prefix_end] + [marker] + body[suffix_start:]
    history[sys_count:] = new_body

    if meta:
        meta(f"[ctx: snipped {dropped} middle messages]")
    return dropped
