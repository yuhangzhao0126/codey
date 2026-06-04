"""UI package — re-exports the TUI app + the four modal screens for tests."""

from .app import CodeyApp, main, run
from .modals.approval import ApprovalScreen
from .modals.mode_picker import ModePickerScreen
from .modals.profile_picker import ProfilePickerScreen
from .modals.remember import RememberScreen
from .slash_suggest import SlashSuggest

__all__ = [
    "ApprovalScreen",
    "CodeyApp",
    "ModePickerScreen",
    "ProfilePickerScreen",
    "RememberScreen",
    "SlashSuggest",
    "main",
    "run",
]
