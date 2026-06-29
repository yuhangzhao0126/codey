"""Orchestrator: chains the 4 proactive steps + the reactive path.

`run_proactive` runs at the top of every model round inside Agent.run().
`run_proactive_force_summary` is what the /compact command and the
`compact` model tool call into. `run_reactive` runs from the except branch
when the provider returns PromptTooLong.

Thresholds are bundled into a Thresholds dataclass; parents use
PARENT_THRESHOLDS, children use CHILD_THRESHOLDS.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from openai import AsyncOpenAI

from ..config import Provider
from ..core.messages import Message
from . import budget as _budget
from . import llm as _llm
from . import micro as _micro
from . import reactive as _reactive
from . import snip as _snip
from . import tokens as _tokens

MetaSink = Callable[[str], None]


@dataclass(frozen=True)
class Thresholds:
    tool_result_budget_bytes: int
    snip_threshold_messages: int
    snip_keep_head: int
    snip_keep_tail: int
    micro_keep_recent_tool_results: int


PARENT_THRESHOLDS = Thresholds(
    tool_result_budget_bytes=_budget.TOOL_RESULT_BUDGET_BYTES,
    snip_threshold_messages=_snip.SNIP_THRESHOLD_MESSAGES,
    snip_keep_head=_snip.SNIP_KEEP_HEAD,
    snip_keep_tail=_snip.SNIP_KEEP_TAIL,
    micro_keep_recent_tool_results=_micro.MICRO_KEEP_RECENT_TOOL_RESULTS,
)

CHILD_THRESHOLDS = Thresholds(
    tool_result_budget_bytes=_budget.TOOL_RESULT_BUDGET_BYTES,
    snip_threshold_messages=30,
    snip_keep_head=3,
    snip_keep_tail=25,
    micro_keep_recent_tool_results=5,
)


def _should_llm_compact(history: list[Message], provider: Provider) -> bool:
    threshold = provider.context_window - provider.max_output_tokens - provider.compact_headroom
    return _tokens.estimate(history) > max(threshold, 0)


def _snip_with(*, history, meta, thresholds: Thresholds) -> int:
    if (thresholds.snip_threshold_messages == _snip.SNIP_THRESHOLD_MESSAGES
            and thresholds.snip_keep_head == _snip.SNIP_KEEP_HEAD
            and thresholds.snip_keep_tail == _snip.SNIP_KEEP_TAIL):
        return _snip.run(history=history, meta=meta)
    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    if len(body) <= thresholds.snip_threshold_messages:
        return 0
    prefix_end = _snip._expand_prefix_to_pair_boundary(body, thresholds.snip_keep_head)
    suffix_start = _snip._expand_suffix_to_pair_boundary(
        body, len(body) - thresholds.snip_keep_tail,
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
    history[sys_count:] = body[:prefix_end] + [marker] + body[suffix_start:]
    if meta:
        meta(f"[ctx: snipped {dropped} middle messages]")
    return dropped


def _micro_with(*, history, meta, thresholds: Thresholds) -> int:
    if thresholds.micro_keep_recent_tool_results == _micro.MICRO_KEEP_RECENT_TOOL_RESULTS:
        return _micro.run(history=history, meta=meta)
    keep = thresholds.micro_keep_recent_tool_results
    tool_idxs = [i for i, m in enumerate(history) if m.role == "tool"]
    if len(tool_idxs) <= keep:
        return 0
    cutoff = len(tool_idxs) - keep
    replaced = 0
    for i in tool_idxs[:cutoff]:
        if history[i].content != _micro.PLACEHOLDER:
            history[i].content = _micro.PLACEHOLDER
            replaced += 1
    if meta and replaced:
        meta(f"[ctx: replaced {replaced} old tool result"
             f"{'s' if replaced > 1 else ''} with placeholder]")
    return replaced


async def run_proactive(
    *,
    history: list[Message],
    provider: Provider,
    session_id: str,
    last_round_tool_idxs: list[int],
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
    thresholds: Thresholds = PARENT_THRESHOLDS,
) -> None:
    """Run the 4-step proactive pipeline. Mutates `history` in place."""
    _budget.run(
        history=history, last_round_tool_idxs=last_round_tool_idxs,
        session_id=session_id, meta=meta,
    )
    _snip_with(history=history, meta=meta, thresholds=thresholds)
    _micro_with(history=history, meta=meta, thresholds=thresholds)
    if _should_llm_compact(history, provider):
        await _llm.run(
            history=history, provider=provider, session_id=session_id,
            meta=meta, client=client, recent_files=list(recent_files),
        )


async def run_proactive_force_summary(
    *,
    history: list[Message],
    provider: Provider,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
    thresholds: Thresholds = PARENT_THRESHOLDS,
) -> None:
    """Run steps 1-3 then unconditionally run llm_compact."""
    _budget.run(history=history, last_round_tool_idxs=[],
                session_id=session_id, meta=meta)
    _snip_with(history=history, meta=meta, thresholds=thresholds)
    _micro_with(history=history, meta=meta, thresholds=thresholds)
    await _llm.run(
        history=history, provider=provider, session_id=session_id,
        meta=meta, client=client, recent_files=list(recent_files),
    )


async def run_reactive(
    *,
    history: list[Message],
    provider: Provider,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
) -> None:
    """Wrapper around reactive.run so callers import from one place."""
    await _reactive.run(
        history=history, provider=provider, session_id=session_id,
        meta=meta, client=client, recent_files=list(recent_files),
    )
