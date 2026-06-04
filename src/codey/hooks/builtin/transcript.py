"""Transcript hook: render tool calls + results to whichever UI is hosting.

The hook receives a `writer` callable at registration time. The CLI passes
a function that prints to stdout; the TUI passes one that writes to its
RichLog. The hook itself doesn't know anything about either UI.

Replaces the inline `if isinstance(event, ToolCallRequested): print(...)`
branches that lived in cli.py / tui.py.
"""

from __future__ import annotations

from typing import Any, Callable

from ..registry import HookCallback, HookResult

# Writer is the host-supplied output sink. Two flavors so we can format slightly
# differently per side: tool-call (yellow), ok-result (green), err-result (red),
# meta (dim).
Writer = Callable[[str, str], None]   # (style, text) where style ∈ {"tool", "ok", "err"}


def pre_tool_render_hook(writer: Writer) -> HookCallback:
    def hook(payload: dict[str, Any]) -> HookResult | None:
        tool = payload["tool"]
        args = payload.get("arguments") or {}
        rendered_args = ", ".join(f"{k}={v!r}" for k, v in args.items())
        writer("tool", f"→ {tool}({rendered_args})")
        return None
    return hook


def post_tool_render_hook(writer: Writer) -> HookCallback:
    def hook(payload: dict[str, Any]) -> HookResult | None:
        tool = payload["tool"]
        ok = bool(payload.get("ok"))
        result = payload.get("result") or ""
        style = "ok" if ok else "err"
        tag = "ok" if ok else "err"
        writer(style, f"← {tool} [{tag}] {result}")
        return None
    return hook
