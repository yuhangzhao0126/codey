"""4-option approval modal used by the permission hook.

Returns one of: 'y' (allow once), 'a' (always allow), 'n' (deny once),
'd' (always deny), or 'cancel' (esc).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ApprovalScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("y", "answer('y')", "allow once"),
        Binding("a", "answer('a')", "always allow"),
        Binding("n", "answer('n')", "deny once"),
        Binding("d", "answer('d')", "always deny"),
        Binding("escape", "answer('cancel')", "cancel"),
    ]

    DEFAULT_CSS = """
    ApprovalScreen { align: center middle; }
    #approval-box {
        width: 90; max-width: 95%;
        padding: 1 2;
        background: $panel;
        border: round $warning;
    }
    #approval-title    { color: $warning;    padding-bottom: 1; }
    #approval-requester{ color: $accent;     padding-bottom: 1; }
    #approval-cmd      { color: $text;       padding-bottom: 1; }
    #approval-reason   { color: $text-muted; padding-bottom: 1; }
    #approval-help     { color: $text-muted; }
    """

    def __init__(
        self,
        *,
        tool: str,
        command: str,
        reason: str = "",
        requester: str | None = None,
    ) -> None:
        super().__init__()
        self.tool = tool
        self.command = command
        self.reason = reason
        self.requester = requester

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-box"):
            yield Static(f"⚠  agent wants to use tool [b]{self.tool}[/]", id="approval-title")
            if self.requester:
                yield Static(f"requested by: {self.requester}", id="approval-requester")
            yield Static(f"$ {self.command}", id="approval-cmd")
            if self.reason:
                yield Static(f"reason: {self.reason}", id="approval-reason")
            yield Static(
                "[b]y[/] allow once   [b]a[/] always allow\n"
                "[b]n[/] deny once    [b]d[/] always deny     [b]esc[/] cancel turn",
                id="approval-help",
            )

    def action_answer(self, ans: str) -> None:
        self.dismiss(ans)
