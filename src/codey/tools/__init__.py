"""Tool registry: register the built-in tools here.

Tools no longer take permission state — permissions are enforced by the
PreToolUse hook (see codey.hooks.builtin.permission). Adding a new tool is
a single `reg.register(YourTool())` line.
"""

from __future__ import annotations

from ..core import ToolRegistry
from .apply_edit import ApplyEditTool
from .bash import ApproveFn, BashTool, Verdict
from .compact import CompactTool
from .grep import GrepTool
from .list_dir import ListDirTool
from .load_memory import LoadMemoryTool
from .read_file import ReadFileTool
from .remember_this import RememberThisTool
from .spawn_agent import SpawnAgentTool
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
    "CompactTool",
    "GrepTool",
    "ListDirTool",
    "LoadMemoryTool",
    "ReadFileTool",
    "RememberThisTool",
    "SpawnAgentTool",
    "TodoWriteTool",
    "Verdict",
    "WriteFileTool",
]
