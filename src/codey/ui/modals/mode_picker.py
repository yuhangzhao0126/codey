"""Arrow-key permission-mode picker. Returns the mode value or None."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...permissions import MODE_DESCRIPTIONS, Mode


class ModePickerScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    DEFAULT_CSS = """
    ModePickerScreen { align: center middle; }
    #mode-box {
        width: 90; max-width: 95%;
        max-height: 60%;
        padding: 1 2;
        background: $panel;
        border: round $primary;
    }
    #mode-title { color: $primary; padding-bottom: 1; }
    #mode-help  { color: $text-muted; padding-top: 1; }
    OptionList { background: $panel; border: none; }
    """

    # Order shown top→bottom: safest → most permissive.
    ORDER = [Mode.PARANOID, Mode.READ_ONLY, Mode.SAFE, Mode.YOLO]

    def __init__(self, active: Mode) -> None:
        super().__init__()
        self.active = active

    def compose(self) -> ComposeResult:
        with Vertical(id="mode-box"):
            yield Static("set permission mode", id="mode-title")
            options = []
            initial_index = 0
            for i, mode in enumerate(self.ORDER):
                mark = "[bold]*[/] " if mode == self.active else "  "
                warn = " [b red]⚠[/]" if mode == Mode.YOLO else ""
                label = f"{mark}{mode.value:<10} [dim]{MODE_DESCRIPTIONS[mode]}[/]{warn}"
                options.append(Option(label, id=mode.value))
                if mode == self.active:
                    initial_index = i
            yield OptionList(*options, id="mode-list")
            yield Static("↑/↓ move · enter select · esc cancel", id="mode-help")
            self._initial_index = initial_index

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.highlighted = self._initial_index
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
