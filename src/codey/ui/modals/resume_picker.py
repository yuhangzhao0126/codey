"""Modal that lists recent sessions for the current workspace."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...session_store import SessionMeta


class ResumePickerScreen(ModalScreen[str | None]):
    """Pick a session id to resume. Returns None on Esc."""

    CSS = """
    ResumePickerScreen { align: center middle; }
    #resume-box {
        width: 88; max-height: 24; padding: 1 2;
        background: $background; border: thick $primary;
    }
    #resume-title { color: $primary; padding-bottom: 1; }
    OptionList { height: auto; max-height: 18; }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(self, metas: list[SessionMeta]) -> None:
        super().__init__()
        self._metas = metas

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-box"):
            yield Static(
                f"resume a session ({len(self._metas)} in this workspace)",
                id="resume-title",
            )
            opts = [Option(self._render(m), id=m.session_id) for m in self._metas]
            yield OptionList(*opts, id="resume-list")
            yield Static("enter to resume · esc to cancel", id="resume-help")

    @staticmethod
    def _render(m: SessionMeta) -> str:
        title = (m.title or "(no title)")[:50]
        return f"{m.session_id}  {m.last_at[:19]}  {m.message_count:>3} msgs  {title!r}"

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(str(ev.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)
