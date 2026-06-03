"""Tool registry: register the built-in tools here.

Importing this package gives you a fully-populated `ToolRegistry`.
"""

from __future__ import annotations

from ..agent import ToolRegistry
from .apply_edit import ApplyEditTool
from .bash import BashTool
from .grep import GrepTool
from .list_dir import ListDirTool
from .read_file import ReadFileTool
from .write_file import WriteFileTool


def build_default_registry(approve=None) -> ToolRegistry:
    """Return a registry pre-populated with the built-in tools.

    `approve(command: str) -> bool` is called by tools that need user consent
    (bash for non-allowlisted commands; write_file and apply_edit for every
    write). If None, anything that would normally prompt is auto-approved —
    fine for tests, NOT for the REPL.
    """
    reg = ToolRegistry()
    reg.register(BashTool(approve=approve))
    reg.register(ReadFileTool())
    reg.register(ListDirTool())
    reg.register(GrepTool())
    reg.register(WriteFileTool(approve=approve))
    reg.register(ApplyEditTool(approve=approve))
    return reg


__all__ = [
    "build_default_registry",
    "ApplyEditTool",
    "BashTool",
    "GrepTool",
    "ListDirTool",
    "ReadFileTool",
    "WriteFileTool",
]
