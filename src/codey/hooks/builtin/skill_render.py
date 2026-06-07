"""Post hook that renders a meta line for each `load_skill` call.

Lives on the PARENT agent's hook registry. Output is one line per call:

    ↳ skill loaded: code-review
    ✗ skill load failed: nope — no skill named 'nope'

PostToolUse-only (no Pre line) because skill loading is an O(1) in-memory
read that returns immediately — there's no useful "starting…" beat the
user would see before the "done" line lands. The hook is a no-op for any
tool other than `load_skill`.
"""

from __future__ import annotations

from typing import Any, Callable

from ..registry import HookCallback, HookResult

Writer = Callable[[str], None]


def build_skill_render_hook(writer: Writer) -> HookCallback:
    def hook(payload: dict[str, Any]) -> HookResult | None:
        if payload.get("tool") != "load_skill":
            return None
        args = payload.get("arguments") or {}
        name = (args.get("name") or "").strip() or "(unknown)"
        result = payload.get("result") or ""
        is_error = (not payload.get("ok")) or (
            isinstance(result, str) and result.startswith("error:")
        )
        if not is_error:
            writer(f"↳ skill loaded: {name}")
            return None
        # Strip the "error:" prefix and trim — the user just needs the gist.
        msg = result if isinstance(result, str) else ""
        if msg.startswith("error:"):
            msg = msg[len("error:"):].strip()
        # Trim at first sentence break to keep the line short.
        msg = msg.split(".", 1)[0].strip()
        msg = msg[:80]
        if msg:
            writer(f"✗ skill load failed: {name} — {msg}")
        else:
            writer(f"✗ skill load failed: {name}")
        return None
    return hook
