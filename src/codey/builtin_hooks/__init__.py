"""Back-compat shim: codey.builtin_hooks → codey.hooks.builtin.

This file is removed in step 10 of the structural refactor. Until then,
existing imports like `from codey.builtin_hooks.permission import …` keep
working unchanged. New code should target codey.hooks.builtin.
"""

from __future__ import annotations

from ..hooks.builtin import (  # noqa: F401  (re-export)
    TodoWriter,
    audit_log_hook,
    build_default_hooks,
    build_todo_nag_hooks,
    permission_check_hook,
    post_tool_render_hook,
    pre_tool_render_hook,
    stop_logger_hook,
    todo_render_hook,
)

__all__ = [
    "TodoWriter",
    "audit_log_hook",
    "build_default_hooks",
    "build_todo_nag_hooks",
    "permission_check_hook",
    "post_tool_render_hook",
    "pre_tool_render_hook",
    "stop_logger_hook",
    "todo_render_hook",
]
