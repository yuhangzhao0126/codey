"""Rendering helpers + UI sinks for the TUI.

This module owns every "write to RichLog" call site, plus the closures that
feed the host-supplied writers into build_default_hooks. The CodeyApp calls
these helpers; they don't reach back into app state beyond the RichLog
reference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from rich.markup import escape as rich_escape

if TYPE_CHECKING:
    from textual.widgets import RichLog


# ---------- per-line rendering helpers ----------

def log_meta(transcript: "RichLog", text: str) -> None:
    transcript.write(f"[dim]{rich_escape(text)}[/]")


def log_user(transcript: "RichLog", text: str) -> None:
    transcript.write(f"[bold cyan]you  ›[/] {text}")


def log_assistant(transcript: "RichLog", text: str) -> None:
    transcript.write(f"[bold magenta]codey›[/] {text}")


def log_tool_call(transcript: "RichLog", name: str, args: dict) -> None:
    rendered_args = ", ".join(f"{k}={v!r}" for k, v in args.items())
    transcript.write(f"  [bold yellow]→ {name}[/]([dim]{rendered_args}[/])")


def log_tool_result(transcript: "RichLog", name: str, ok: bool, content: str) -> None:
    tag_color = "green" if ok else "red"
    tag = "ok" if ok else "err"
    body = "\n".join(f"    [dim]{line}[/]" for line in content.splitlines() or [""])
    transcript.write(f"  [bold {tag_color}]← {name} [{tag}][/]\n{body}")


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
