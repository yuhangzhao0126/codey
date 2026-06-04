"""Hooks package — re-exports the HookEvent / HookResult / HookRegistry
public API from .registry so every existing `from codey.hooks import …`
keeps working after the relocation.

The builtin hooks live under codey.hooks.builtin (formerly codey.builtin_hooks);
a back-compat shim package at src/codey/builtin_hooks/ re-exports the same
names for one cycle.
"""

from __future__ import annotations

from .registry import (
    Hook,
    HookCallback,
    HookEvent,
    HookRegistry,
    HookResult,
)

__all__ = [
    "Hook",
    "HookCallback",
    "HookEvent",
    "HookRegistry",
    "HookResult",
]
