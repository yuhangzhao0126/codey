"""Tool registry: register the built-in tools here.

Importing this package gives you a fully-populated `ToolRegistry`. Pass an
engine for permission gating; pass an approve callback for tools that may
need to ask the user.
"""

from __future__ import annotations

from ..agent import ToolRegistry
from ..permissions import PermissionEngine
from .apply_edit import ApplyEditTool
from .bash import ApproveFn, BashTool, Verdict
from .grep import GrepTool
from .list_dir import ListDirTool
from .read_file import ReadFileTool
from .write_file import WriteFileTool


def build_default_registry(
    engine: PermissionEngine | None = None,
    approve: ApproveFn | None = None,
) -> ToolRegistry:
    """Return a registry pre-populated with the built-in tools.

    `engine` is the permission engine (defaults to a permissive in-memory one).
    `approve` is called for tools that get an Ask decision. If None, anything
    that would normally prompt is auto-approved — fine for tests, NOT for the REPL.
    """
    if engine is None:
        engine = PermissionEngine()
    reg = ToolRegistry()
    reg.register(BashTool(engine=engine, approve=approve))
    reg.register(ReadFileTool())
    reg.register(ListDirTool())
    reg.register(GrepTool())
    reg.register(WriteFileTool(engine=engine, approve=approve))
    reg.register(ApplyEditTool(engine=engine, approve=approve))
    return reg


__all__ = [
    "build_default_registry",
    "ApplyEditTool",
    "ApproveFn",
    "BashTool",
    "GrepTool",
    "ListDirTool",
    "ReadFileTool",
    "Verdict",
    "WriteFileTool",
]
