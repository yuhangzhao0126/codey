"""Modal panel that lists the sub-agents spawned during this session.

Backed by Session.subagent_recorder. Snapshot-on-open — no live tail. Two
internal screens:

  list view   one line per child: label · state · N rounds
  detail view full event timeline for the selected child

The recorder caps events per child (default 10k), so very long-running
children get their tail-end events; we surface that via a "[head truncated:
showing last K events]" line at the top of the detail view if applicable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, RichLog, Static
from textual.widgets.option_list import Option

from rich.markup import escape as rich_escape

if TYPE_CHECKING:
    from ...core.session import SubAgentRecorder


def _summarize_child(events: list[Any]) -> tuple[str, int]:
    """Return (state, round_count) derived from the captured event stream."""
    # Lazy imports so this module doesn't pull core/events at import time.
    from ...core.events import RoundStarted, TurnCompleted

    state = "running"
    rounds = 0
    for ev in events:
        if isinstance(ev, RoundStarted):
            rounds += 1
        elif isinstance(ev, TurnCompleted):
            state = ev.reason
    return state, rounds


def _format_event(ev: Any) -> str:
    """One Rich-markup line per event for the detail view."""
    from ...core.events import (
        AssistantMessageCompleted,
        AssistantTextDelta,
        RoundStarted,
        ToolCallRequested,
        ToolResult,
        TurnCompleted,
        TurnStarted,
    )

    if isinstance(ev, TurnStarted):
        return "[dim]── turn started ──[/]"
    if isinstance(ev, RoundStarted):
        return f"[dim]── round {ev.round} ──[/]"
    if isinstance(ev, AssistantTextDelta):
        return f"[magenta]δ[/] {rich_escape(ev.text)}"
    if isinstance(ev, AssistantMessageCompleted):
        body = rich_escape(ev.text or "(empty)")
        return f"[bold magenta]codey›[/] {body}"
    if isinstance(ev, ToolCallRequested):
        args_preview = rich_escape(str(ev.arguments))
        if len(args_preview) > 200:
            args_preview = args_preview[:200] + "…"
        return f"[yellow]→[/] {ev.name}({args_preview})"
    if isinstance(ev, ToolResult):
        marker = "[green]←[/]" if ev.ok else "[red]←[/]"
        body = rich_escape(ev.content or "")
        if len(body) > 400:
            body = body[:400] + "…"
        return f"{marker} {ev.name} {body}"
    if isinstance(ev, TurnCompleted):
        if ev.reason == "stop":
            return "[dim]── turn finished ──[/]"
        err = rich_escape(ev.error or "")
        return f"[red]── turn {ev.reason}: {err} ──[/]"
    return f"[dim]{type(ev).__name__}[/]"


class SubAgentPanelScreen(ModalScreen[None]):
    """Modal /subs panel. Returns None (dismiss-only)."""

    BINDINGS = [Binding("escape", "back", "back/close")]

    DEFAULT_CSS = """
    SubAgentPanelScreen { align: center middle; }
    #subs-box {
        width: 110; max-width: 95%;
        height: 80%;
        padding: 1 2;
        background: $panel;
        border: round $primary;
    }
    #subs-title  { color: $primary; padding-bottom: 1; }
    #subs-help   { color: $text-muted; padding-top: 1; }
    #subs-list   { background: $panel; border: none; height: 1fr; }
    #subs-detail { background: $panel; border: none; height: 1fr; }
    """

    def __init__(self, recorder: "SubAgentRecorder") -> None:
        super().__init__()
        self.recorder = recorder
        # Snapshot the children order at open time; events_for() re-reads
        # but order stays stable within one open.
        self.child_ids: list[str] = list(recorder.children())
        self._active_child: str | None = None  # set when drilling in

    def compose(self) -> ComposeResult:
        with Vertical(id="subs-box"):
            yield Static("sub-agents (this session)", id="subs-title")
            if not self.child_ids:
                yield Static("[dim](no sub-agents spawned yet)[/]")
                yield Static("esc close", id="subs-help")
                return
            options = []
            for i, child_id in enumerate(self.child_ids, start=1):
                events = self.recorder.events_for(child_id)
                state, rounds = _summarize_child(events)
                label = self.recorder.label_for(child_id)
                state_markup = self._state_markup(state)
                line = (
                    f"  [bold]{i}.[/] {rich_escape(label):<40} "
                    f"{state_markup}  [dim]{rounds} rounds[/]"
                )
                options.append(Option(line, id=child_id))
            yield OptionList(*options, id="subs-list")
            yield Static(
                "↑/↓ move · enter open · esc close", id="subs-help"
            )

    def on_mount(self) -> None:
        if self.child_ids:
            ol = self.query_one("#subs-list", OptionList)
            ol.highlighted = 0
            ol.focus()

    @staticmethod
    def _state_markup(state: str) -> str:
        if state == "stop":
            return "[green]done[/]"
        if state == "error":
            return "[red]error[/]"
        if state == "cancelled":
            return "[yellow]cancelled[/]"
        return "[blue]running[/]"

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Open the detail view in place by swapping the body.
        child_id = event.option.id
        if not child_id:
            return
        self._show_detail(child_id)

    def _show_detail(self, child_id: str) -> None:
        self._active_child = child_id
        events = self.recorder.events_for(child_id)
        cap = getattr(self.recorder, "per_child_cap", 0)
        truncated = cap and len(events) == cap
        label = self.recorder.label_for(child_id)
        state, rounds = _summarize_child(events)

        box = self.query_one("#subs-box", Vertical)
        # Wipe out the list-view widgets and rebuild as detail view.
        # remove() is async — we await via Textual's remove_children to make
        # the mount() below safe to reuse the old IDs.
        box.remove_children()
        box.mount(Static(f"sub-agent: [bold]{rich_escape(label)}[/]  "
                         f"{self._state_markup(state)}  [dim]{rounds} rounds[/]",
                         id="subs-detail-title"))
        log = RichLog(id="subs-detail", wrap=True, markup=True,
                      highlight=False, auto_scroll=False)
        box.mount(log)
        if truncated:
            log.write(f"[dim](showing last {len(events)} events — older "
                      f"events were dropped at the per-child cap)[/]")
        for ev in events:
            log.write(_format_event(ev))
        box.mount(Static("esc back", id="subs-detail-help"))

    def action_back(self) -> None:
        if self._active_child is not None:
            # Detail view → close (rebuilding the list view on Esc would be
            # nice but the events are stale anyway; one Esc closes the modal).
            self.dismiss(None)
            return
        self.dismiss(None)
