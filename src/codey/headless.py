"""Headless single-shot runner: one prompt, stream to stdout, exit. No TUI.

For eval/benchmark in containers. Forces YOLO (no stdin to approve). Built-in
deny rules still apply. Exit 0 on natural stop, 1 on error/cancel.
"""

from __future__ import annotations

import sys

from .config import ConfigFile
from .core.events import AssistantTextDelta, ToolCallRequested, ToolResult, TurnCompleted
from .core.session import Session
from .permissions import Mode, Verdict
from .ui.renderers import UISinks


async def run_headless(prompt: str, *, provider_arg: str | None, otel: bool = False) -> int:
    sinks = UISinks(
        meta_writer=lambda s: print(s, file=sys.stderr, flush=True),
        approve=_deny,  # yolo never calls this; deny if something slips through
    )
    try:
        sess = Session.build(provider_arg=provider_arg, ui_sinks=sinks, otel_enabled=otel)
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    sess.engine.mode = Mode.YOLO

    rc = 0
    try:
        async for ev in sess.agent.run(prompt):
            if isinstance(ev, AssistantTextDelta):
                sys.stdout.write(ev.text)
                sys.stdout.flush()
            elif isinstance(ev, ToolCallRequested):
                print(f"\n[tool] {ev.name} {ev.arguments}", file=sys.stderr, flush=True)
            elif isinstance(ev, ToolResult):
                tag = "ok" if ev.ok else "err"
                print(f"[{tag}] {ev.name}: {str(ev.content)[:200]}", file=sys.stderr, flush=True)
            elif isinstance(ev, TurnCompleted):
                print()  # trailing newline after streamed text
                if ev.reason != "stop":
                    print(f"error: {ev.error or ev.reason}", file=sys.stderr)
                    rc = 1
    finally:
        await sess.aclose()
    return rc


async def _deny(_ctx: dict) -> Verdict:
    return Verdict(allowed=False)
