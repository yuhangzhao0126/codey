"""Stop hook: log one line at the end of each turn.

Foundation for a future auto-summary hook. For now it just shows the reason
so users see something even when the model output was short.
"""

from __future__ import annotations

from typing import Any, Callable

from ..registry import HookCallback, HookResult

Writer = Callable[[str], None]


def stop_logger_hook(writer: Writer) -> HookCallback:
    def hook(payload: dict[str, Any]) -> HookResult | None:
        reason = payload.get("reason") or "unknown"
        error = payload.get("error")
        msg = f"[turn finished: {reason}]"
        if error and reason == "error":
            msg = f"[turn finished: error — {error}]"
        writer(msg)
        return None
    return hook
