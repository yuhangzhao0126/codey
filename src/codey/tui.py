"""Back-compat shim for `from codey.tui import …`.

The TUI now lives under `codey.ui`. This module re-exports the same public
surface (CodeyApp, modal screens, SlashSuggest, run, main) so existing
imports keep working. Removed in step 10 of the refactor.
"""

from __future__ import annotations

from .ui import (  # noqa: F401
    ApprovalScreen,
    CodeyApp,
    ModePickerScreen,
    ProfilePickerScreen,
    RememberScreen,
    SlashSuggest,
    main,
    run,
)
from .ui.renderers import _make_tui_todo_writer  # noqa: F401

__all__ = [
    "ApprovalScreen",
    "CodeyApp",
    "ModePickerScreen",
    "ProfilePickerScreen",
    "RememberScreen",
    "SlashSuggest",
    "_make_tui_todo_writer",
    "main",
    "run",
]
