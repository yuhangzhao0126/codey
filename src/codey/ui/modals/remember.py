"""Modal for capturing pattern + scope when the user picks 'always allow' or
'always deny' in the ApprovalScreen.

Returns (pattern, scope) or None on cancel.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class RememberScreen(ModalScreen[tuple[str, str] | None]):
    BINDINGS = [Binding("escape", "cancel", "cancel")]

    DEFAULT_CSS = """
    RememberScreen { align: center middle; }
    #remember-box {
        width: 90; max-width: 95%;
        padding: 1 2;
        background: $panel;
        border: round $primary;
    }
    #remember-title  { color: $primary; padding-bottom: 1; }
    #remember-help   { color: $text-muted; padding-top: 1; }
    Input { background: $background; border: round $primary 50%; }
    """

    def __init__(self, action: str, suggested_pattern: str) -> None:
        super().__init__()
        self.action = action
        self.suggested = suggested_pattern

    def compose(self) -> ComposeResult:
        with Vertical(id="remember-box"):
            yield Static(f"save '{self.action}' rule", id="remember-title")
            yield Static("pattern (glob, * matches anything):")
            yield Input(value=self.suggested, id="remember-pattern")
            yield Static("scope: type 'project' or 'user' (default project)")
            yield Input(value="project", id="remember-scope")
            yield Static("enter twice to save · esc to cancel", id="remember-help")

    def on_mount(self) -> None:
        self.query_one("#remember-pattern", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "remember-pattern":
            self.query_one("#remember-scope", Input).focus()
            return
        pattern = self.query_one("#remember-pattern", Input).value.strip() or self.suggested
        scope = self.query_one("#remember-scope", Input).value.strip().lower() or "project"
        scope = "user" if scope.startswith("u") else "project"
        self.dismiss((pattern, scope))

    def action_cancel(self) -> None:
        self.dismiss(None)
