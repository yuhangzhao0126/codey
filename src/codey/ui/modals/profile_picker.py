"""Arrow-key profile picker modal. Returns the chosen profile name or None."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...config import ConfigFile


class ProfilePickerScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    DEFAULT_CSS = """
    ProfilePickerScreen { align: center middle; }
    #picker-box {
        width: 80; max-width: 90%;
        max-height: 60%;
        padding: 1 2;
        background: $panel;
        border: round $primary;
    }
    #picker-title { color: $primary; padding-bottom: 1; }
    #picker-help  { color: $text-muted; padding-top: 1; }
    OptionList { background: $panel; border: none; }
    """

    def __init__(self, cfg: ConfigFile, active: str) -> None:
        super().__init__()
        self.cfg = cfg
        self.active = active

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static("switch profile", id="picker-title")
            options = []
            initial_index = 0
            for i, name in enumerate(sorted(self.cfg.profiles)):
                p = self.cfg.profiles[name]
                mark = "[bold]*[/] " if name == self.active else "  "
                label = f"{mark}{name}  [dim]{p.model} @ {p.base_url}[/]"
                options.append(Option(label, id=name))
                if name == self.active:
                    initial_index = i
            ol = OptionList(*options, id="picker-list")
            yield ol
            yield Static("↑/↓ move · enter select · esc cancel", id="picker-help")
            self._initial_index = initial_index

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        ol.highlighted = self._initial_index
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
