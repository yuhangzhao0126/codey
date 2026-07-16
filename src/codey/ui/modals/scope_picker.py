"""Modal that chooses the scope for the resume picker: this workspace vs all."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class ScopePickerScreen(ModalScreen[str | None]):
    """Pick which sessions to list. Returns 'workspace', 'global', or None (Esc)."""

    CSS = """
    ScopePickerScreen { align: center middle; }
    #scope-box {
        width: 60; padding: 1 2;
        background: $background; border: thick $primary;
    }
    #scope-title { color: $primary; padding-bottom: 1; }
    OptionList { height: auto; }
    #scope-help { padding-top: 1; color: $text-muted; }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, workspace_count: int, global_count: int) -> None:
        super().__init__()
        self._ws_count = workspace_count
        self._global_count = global_count

    def compose(self) -> ComposeResult:
        with Vertical(id="scope-box"):
            yield Static("resume a session — choose scope", id="scope-title")
            yield OptionList(
                Option(f"This workspace  ({self._ws_count})", id="workspace"),
                Option(f"All workspaces  ({self._global_count})", id="global"),
                id="scope-list",
            )
            yield Static("enter to choose · esc to cancel", id="scope-help")

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(str(ev.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)
