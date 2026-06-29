"""First-run prompt: ask for a DeepSeek API key. Returns the key, or None to skip."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class FirstRunScreen(ModalScreen[str | None]):
    """Shown when the active profile still has the placeholder API key."""

    BINDINGS = [Binding("escape", "skip", "skip")]

    DEFAULT_CSS = """
    FirstRunScreen { align: center middle; }
    #fr-box {
        width: 80; max-width: 90%;
        padding: 1 2;
        background: $panel;
        border: round $primary;
    }
    #fr-title { color: $primary; padding-bottom: 1; }
    #fr-help  { color: $text-muted; padding-top: 1; }
    Input { background: $panel; }
    """

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path

    def compose(self) -> ComposeResult:
        with Vertical(id="fr-box"):
            yield Static("welcome to codey — no API key set yet", id="fr-title")
            yield Input(placeholder="paste your DeepSeek API key (or press esc to skip)",
                        password=True, id="fr-input")
            yield Static(f"add other providers later in {self.config_path}", id="fr-help")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        key = event.value.strip()
        self.dismiss(key or None)

    def action_skip(self) -> None:
        self.dismiss(None)
