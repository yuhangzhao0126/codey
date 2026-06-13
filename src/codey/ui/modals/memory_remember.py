"""Modal for /remember: confirms a parsed candidate before saving."""
from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


@dataclass
class MemoryDraft:
    name: str
    description: str
    body: str
    type: str
    scope: str  # "global" | "project"


class MemoryRememberScreen(ModalScreen["MemoryDraft | None"]):
    """Confirm / edit a memory draft before saving. Returns draft or None."""

    CSS = """
    MemoryRememberScreen { align: center middle; }
    #mem-box { width: 90; padding: 1 2; background: $background; border: thick $primary; }
    #mem-title { color: $primary; padding-bottom: 1; }
    #mem-help  { color: $text-muted; padding-top: 1; }
    Input { margin-bottom: 1; }
    """

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        ("ctrl+s", "save", "save"),
    ]

    def __init__(self, draft: MemoryDraft) -> None:
        super().__init__()
        self._draft = draft

    def compose(self) -> ComposeResult:
        with Vertical(id="mem-box"):
            yield Static("save memory entry — review & confirm", id="mem-title")
            yield Static("name (snake_case):")
            yield Input(value=self._draft.name, id="mem-name")
            yield Static("description:")
            yield Input(value=self._draft.description, id="mem-desc")
            yield Static("body:")
            yield Input(value=self._draft.body, id="mem-body")
            yield Static("type (preference|project|fact|style|other):")
            yield Input(value=self._draft.type, id="mem-type")
            yield Static("scope (global|project):")
            yield Input(value=self._draft.scope, id="mem-scope")
            yield Static("ctrl+s save · esc cancel", id="mem-help")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        d = MemoryDraft(
            name=self.query_one("#mem-name", Input).value.strip(),
            description=self.query_one("#mem-desc", Input).value.strip(),
            body=self.query_one("#mem-body", Input).value.strip(),
            type=self.query_one("#mem-type", Input).value.strip() or "other",
            scope=self.query_one("#mem-scope", Input).value.strip() or "project",
        )
        self.dismiss(d)
