"""UI package — re-exports the TUI app + the four modal screens for tests."""

from .app import CodeyApp, main, run
from .modals.approval import ApprovalScreen
from .modals.mode_picker import ModePickerScreen
from .modals.provider_picker import ProviderPickerScreen
from .modals.remember import RememberScreen
from .modals.subagent_panel import SubAgentPanelScreen
from .slash_suggest import SlashSuggest

__all__ = [
    "ApprovalScreen",
    "CodeyApp",
    "ModePickerScreen",
    "ProviderPickerScreen",
    "RememberScreen",
    "SlashSuggest",
    "SubAgentPanelScreen",
    "main",
    "run",
]
