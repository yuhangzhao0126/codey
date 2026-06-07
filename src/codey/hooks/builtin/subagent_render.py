"""Pre/Post hook that renders a meta line for each `spawn_agent` call.

Lives on the PARENT agent's hook registry — children never render this (they
have their own curated hook set and would otherwise double-print). Output:

    ⏵ sub-agent[1] "investigate-auth"
    ⏵ sub-agent[2] "investigate-db"
    ... (concurrent silence) ...
    ⏷ sub-agent[1] done (11.7s)
    ⏷ sub-agent[2] done (10.3s)

With concurrent dispatch (one `asyncio.gather` per round in `Agent.run()`),
the two `⏵` lines fire together and the two `⏷` lines fire together once
both children resolve.

The hook is a no-op for any tool other than `spawn_agent`.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..registry import HookCallback, HookResult

Writer = Callable[[str], None]


def build_subagent_render_hooks(writer: Writer) -> tuple[HookCallback, HookCallback]:
    """Return (pre_hook, post_hook). The pre hook assigns each spawn_agent call
    a 1-based index and stamps its start time; the post hook reads back the
    timing and emits the done line."""
    counter = 0
    # call_id → (index, start_monotonic, description)
    pending: dict[str, tuple[int, float, str]] = {}

    def pre(payload: dict[str, Any]) -> HookResult | None:
        if payload.get("tool") != "spawn_agent":
            return None
        nonlocal counter
        counter += 1
        call_id = payload.get("call_id") or ""
        args = payload.get("arguments") or {}
        desc = (args.get("description") or "").strip() or "(no description)"
        pending[call_id] = (counter, time.monotonic(), desc)
        writer(f"⏵ sub-agent[{counter}] \"{desc}\"")
        return None

    def post(payload: dict[str, Any]) -> HookResult | None:
        if payload.get("tool") != "spawn_agent":
            return None
        call_id = payload.get("call_id") or ""
        entry = pending.pop(call_id, None)
        if entry is None:
            return None
        idx, t0, desc = entry
        elapsed = time.monotonic() - t0
        result = payload.get("result") or ""
        if not payload.get("ok") or (isinstance(result, str) and result.startswith("error:")):
            tail = result if isinstance(result, str) else ""
            tail = tail.split("\n", 1)[0][:80]
            writer(f"⏷ sub-agent[{idx}] failed ({elapsed:.1f}s) — {tail}")
        else:
            writer(f"⏷ sub-agent[{idx}] done ({elapsed:.1f}s)")
        return None

    return pre, post
