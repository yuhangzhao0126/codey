"""todo_render: hand the current task list to a UI-supplied writer after
each successful todo_write call.

The writer signature is `Callable[[list[Todo]], None]`. The host (CLI/TUI)
supplies a writer that formats the list however it likes — plain text for
the REPL, Rich markup for the TUI.
"""

from __future__ import annotations

from typing import Any, Callable

from ..registry import HookCallback, HookResult
from ...tools.todo_write import Todo, TodoWriteTool

TodoWriter = Callable[[list[Todo]], None]


def todo_render_hook(*, tool: TodoWriteTool, writer: TodoWriter) -> HookCallback:
    def hook(payload: dict[str, Any]) -> HookResult | None:
        if payload.get("tool") != "todo_write":
            return None
        if not payload.get("ok"):
            return None
        try:
            writer(list(tool.todos))
        except Exception:  # noqa: BLE001
            pass
        return None
    return hook
