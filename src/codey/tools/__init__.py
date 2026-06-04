"""Tool registry: register the built-in tools here.

Tools no longer take permission state — permissions are enforced by the
PreToolUse hook (see codey.hooks.builtin.permission). Adding a new tool is
a single `reg.register(YourTool())` line.
"""

from __future__ import annotations

from ..core import ToolRegistry
from .apply_edit import ApplyEditTool
from .bash import ApproveFn, BashTool, Verdict
from .grep import GrepTool
from .list_dir import ListDirTool
from .read_file import ReadFileTool
from .todo_write import TodoWriteTool
from .write_file import WriteFileTool


def build_default_registry() -> ToolRegistry:
    """Return a registry pre-populated with the built-in tools."""
    reg = ToolRegistry()
    reg.register(BashTool())
    reg.register(ReadFileTool())
    reg.register(ListDirTool())
    reg.register(GrepTool())
    reg.register(WriteFileTool())
    reg.register(ApplyEditTool())
    reg.register(TodoWriteTool())
    return reg


__all__ = [
    "build_default_registry",
    "ApplyEditTool",
    "ApproveFn",
    "BashTool",
    "GrepTool",
    "ListDirTool",
    "ReadFileTool",
    "TodoWriteTool",
    "Verdict",
    "WriteFileTool",
]
