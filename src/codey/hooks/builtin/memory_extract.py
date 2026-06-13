"""STOP-hook callback: fire-and-forget memory extractor.

Reads the just-finished turn's messages, runs propose_candidates ->
targeted_consolidate -> MemoryStore.write. Each scheduled task records a
queue line first (crash recovery on next Session.build) and acks on
success. Never blocks the turn.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

from ...core.messages import Message
from ...memory.consolidate import targeted_consolidate
from ...memory.extract import propose_candidates
from ...memory.queue import ack, enqueue
from ...memory.registry import MemoryRegistry
from ...memory.store import MemoryStore
from ..registry import HookResult

HistoryProvider = Callable[[], list[Message]]
ClientProvider = Callable[[], Any]


def build_memory_extract_hook(
    *,
    history_provider: HistoryProvider,
    registry: MemoryRegistry,
    store: MemoryStore,
    client_provider: ClientProvider,
    model: str,
    session_id: str,
    queue_path: Path,
    meta: Callable[[str], None] | None = None,
):
    async def cb(payload: dict) -> HookResult | None:
        history = list(history_provider())
        msgs = [m for m in history if m.role != "system"]
        if not msgs:
            return None

        async def _run() -> None:
            line_id = enqueue(queue_path, payload={
                "session_id": session_id,
                "turn_message_count": len(msgs),
            })
            try:
                client = client_provider()
                candidates = await propose_candidates(
                    msgs,
                    existing_index=registry.list_meta(),
                    client=client, model=model,
                )
                for c in candidates:
                    try:
                        await targeted_consolidate(
                            c, registry=registry, store=store,
                            session_id=session_id, client=client, model=model,
                        )
                    except Exception as e:  # noqa: BLE001
                        if meta:
                            meta(f"[memory: consolidate failed for {c.name!r}: "
                                 f"{type(e).__name__}: {e}]")
                if meta and candidates:
                    names = ", ".join(c.name for c in candidates)
                    meta(f"[memory: extracted {names}]")
            except Exception as e:  # noqa: BLE001
                if meta:
                    meta(f"[memory: extract failed: {type(e).__name__}: {e}]")
            finally:
                ack(queue_path, line_id=line_id)

        task = asyncio.create_task(_run())
        cb._last_task = task  # type: ignore[attr-defined]
        return None

    cb._last_task = None  # type: ignore[attr-defined]
    return cb
