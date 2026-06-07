"""Step 1: tool_result_budget — persist big tool results to disk.

Triggered when the sum of byte-sizes of the last round's tool messages
exceeds 200kb. Each oversized round writes every tool result body to a
file under ~/.cache/codey/transcripts/<session_id>/tool_results/ and
replaces the in-history content with a short `<persisted output>` stub.

Idempotent: messages whose content already starts with PERSISTED_STUB_PREFIX
are skipped.
"""
from __future__ import annotations

from typing import Callable

from ..core.messages import Message
from . import transcripts as _transcripts

TOOL_RESULT_BUDGET_BYTES = 200_000
TOOL_RESULT_PERSIST_PREVIEW_CHARS = 2_000
PERSISTED_STUB_PREFIX = "<persisted output>"

MetaSink = Callable[[str], None]


def _persisted_stub(*, path, original_bytes: int, body: str) -> str:
    preview = body[:TOOL_RESULT_PERSIST_PREVIEW_CHARS]
    return (
        f"{PERSISTED_STUB_PREFIX}\n"
        f"path: {path}\n"
        f"original_bytes: {original_bytes}\n"
        f"preview (first {len(preview)} of {len(body)} chars):\n"
        f"{preview}"
    )


def run(
    *,
    history: list[Message],
    last_round_tool_idxs: list[int],
    session_id: str,
    meta: MetaSink | None,
) -> int:
    """Persist last-round tool results past the budget. Returns # persisted."""
    if not last_round_tool_idxs:
        return 0

    candidates: list[tuple[int, int]] = []
    for i in last_round_tool_idxs:
        if not (0 <= i < len(history)):
            continue
        msg = history[i]
        if msg.role != "tool" or not msg.content:
            continue
        if msg.content.startswith(PERSISTED_STUB_PREFIX):
            continue
        candidates.append((i, len(msg.content.encode("utf-8"))))

    total = sum(sz for _, sz in candidates)
    if total <= TOOL_RESULT_BUDGET_BYTES:
        return 0

    persisted = 0
    total_bytes_persisted = 0
    for idx, sz in candidates:
        msg = history[idx]
        try:
            path = _transcripts.write_persisted_tool_result(
                session_id=session_id,
                call_id=msg.tool_call_id or "unknown",
                tool_name=msg.name or "tool",
                body=msg.content,
            )
        except Exception as e:  # noqa: BLE001
            if meta:
                meta(f"[ctx: persist failed for {msg.tool_call_id}: "
                     f"{type(e).__name__}: {e}]")
            continue
        msg.content = _persisted_stub(path=path, original_bytes=sz, body=msg.content)
        persisted += 1
        total_bytes_persisted += sz

    if meta and persisted:
        meta(f"[ctx: persisted {persisted} tool result"
             f"{'s' if persisted > 1 else ''} "
             f"({total_bytes_persisted // 1000}kb) to disk]")
    return persisted
