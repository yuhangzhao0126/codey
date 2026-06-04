"""Rendering helpers + UI sinks for the TUI.

This module owns every "write to RichLog" call site, plus the closures that
feed the host-supplied writers into build_default_hooks. The CodeyApp calls
these helpers; they don't reach back into app state beyond the RichLog
reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from rich.markdown import Markdown
from rich.markup import escape as rich_escape

if TYPE_CHECKING:
    from textual.widgets import RichLog


# ---------- UI-supplied sinks consumed by Session.build() ----------

@dataclass
class UISinks:
    """Concrete bundle of the writers + approver Session.build() needs.

    Session.build accepts this structurally (via codey.core.session.UISinks
    Protocol), so core/ doesn't need to import from ui/.

    `transcript_writer=None` means the per-call → / ← lines are suppressed
    (the default for the post-PR-C quiet TUI); the audit log still records
    every call.
    """
    meta_writer:       Callable[[str], None]
    approve:           Callable[[dict], Awaitable[Any]]
    transcript_writer: Callable[[str, str], None] | None = None
    todo_writer:       Callable[[list], None] | None = None


# ---------- per-line rendering helpers ----------

def log_meta(transcript: "RichLog", text: str) -> None:
    transcript.write(f"[dim]{rich_escape(text)}[/]")


def log_user(transcript: "RichLog", text: str) -> None:
    transcript.write(f"[bold cyan]you  ›[/] {text}")


def log_assistant(transcript: "RichLog", text: str) -> None:
    """Render an assistant reply.

    Writes the `codey›` prefix as one Rich-markup line, then renders the
    reply body as Markdown (headers, lists, fenced code blocks with
    Pygments highlighting, etc.). On any rendering failure (exotic
    Pygments lexer, etc.) fall back to writing the raw text so the user
    always sees something.
    """
    transcript.write("[bold magenta]codey›[/]")
    try:
        transcript.write(Markdown(text))
    except Exception:  # noqa: BLE001
        transcript.write(text)


def log_error(transcript: "RichLog", text: str) -> None:
    transcript.write(f"[bold red]✗ {text}[/]")


# ---------- todo-list writer factory ----------

def make_tui_todo_writer(line_writer: Callable[[str], None]):
    """Build a writer that formats the todo list as Rich markup lines.

    `line_writer(text: str)` is called once per line (header + each item).
    Completed items are dim + strikethrough, in_progress is bold, pending
    is plain.
    """
    def writer(todos):
        if not todos:
            line_writer("[dim]─── tasks (cleared) ───[/dim]")
            return
        line_writer("[dim]─── tasks ───[/dim]")
        for t in todos:
            content = rich_escape(t.content)
            if t.status == "completed":
                line_writer(f"  [dim][x] [strike]{content}[/strike][/dim]")
            elif t.status == "in_progress":
                line_writer(f"  [bold][~] {content}[/bold]")
            else:
                line_writer(f"  [ ] {content}")
    return writer


# Back-compat alias for tests that import the old underscored name from tui.py
_make_tui_todo_writer = make_tui_todo_writer
