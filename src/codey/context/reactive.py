"""Reactive compaction — runs on PromptTooLongError.

Same shape as llm_compact_history but more aggressive: keep system +
summary intro + only the last REACTIVE_TAIL messages (expanded outward
for tool-pair safety). The orchestrator caps this to 1 retry per turn;
a second failure surfaces the original error.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from openai import AsyncOpenAI

from ..config import Profile
from ..core.messages import Message
from . import llm as _llm
from . import snip as _snip
from . import transcripts as _transcripts

REACTIVE_TAIL = 5
REACTIVE_MAX_RETRIES = 1

MetaSink = Callable[[str], None]


async def run(
    *,
    history: list[Message],
    profile: Profile,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
) -> None:
    try:
        snapshot_path = _transcripts.write_history_snapshot(
            session_id=session_id, history=history, kind="reactive",
        )
    except Exception:  # noqa: BLE001
        snapshot_path = None

    summary = await _llm._summarize(client, profile, history)
    file_blocks = _llm._read_recent_files(recent_files, _llm.MAX_RECENT_FILES)

    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    tail_start = max(0, len(body) - REACTIVE_TAIL)
    tail_start = _snip._expand_suffix_to_pair_boundary(body, tail_start)
    tail = body[tail_start:]

    intro = Message(
        role="user",
        content=_llm._build_replacement_user_message(
            summary=summary, file_blocks=file_blocks,
            snapshot_path=snapshot_path,
            header="[Reactive compact triggered after PromptTooLong]",
        ),
    )
    history[:] = list(history[:sys_count]) + [intro] + list(tail)

    if meta:
        meta(f"[ctx: reactive compact triggered (kept last {len(tail)} msgs)]")
