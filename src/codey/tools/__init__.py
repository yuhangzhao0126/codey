"""Tool registry: register the built-in tools here.

Importing this package gives you a fully-populated `ToolRegistry`.
"""

from __future__ import annotations

from ..agent import ToolRegistry
from .bash import BashTool


def build_default_registry(approve=None) -> ToolRegistry:
    """Return a registry pre-populated with the built-in tools.

    `approve(command: str) -> bool` is called by tools that need user consent
    (currently only BashTool for non-allowlisted commands). If None, everything
    that would normally prompt is auto-approved — fine for tests, NOT for the REPL.
    """
    reg = ToolRegistry()
    reg.register(BashTool(approve=approve))
    return reg


__all__ = ["build_default_registry", "BashTool"]
