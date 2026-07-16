"""Modal that lists sessions to resume, with a lazy per-session detail pane."""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ...session_store import SessionMeta

# preview_fn(session_id) -> list of user-prompt strings (first 2 + last 1)
PreviewFn = Callable[[str], list[str]]


def render_row(m: SessionMeta, unavailable_reason: str | None) -> str:
    """One list row. Marks unavailable sessions and dims them via Rich markup."""
    base = f"{m.session_id}  {m.last_at[:19]}  {m.message_count:>3} msgs"
    if unavailable_reason:
        return f"[dim]{base}  [unavailable: {unavailable_reason}][/dim]"
    return base


def format_detail(m: SessionMeta, prompts: list[str],
                  unavailable_reason: str | None) -> str:
    """The right-hand detail pane text for a highlighted session."""
    lines = [
        f"[b]session[/b]  {m.session_id}",
        f"[b]dir[/b]     {m.workspace}",
        f"[b]provider[/b] {m.provider}   ·   {m.message_count} msgs"
        f"   ·   {m.last_at[:19]}",
    ]
    if unavailable_reason:
        lines.append("")
        lines.append(f"[red]cannot resume: {unavailable_reason}[/red]")
    lines.append("")
    if prompts:
        # first up to 2 shown, then a separator, then the last one.
        for p in prompts[:-1] if len(prompts) > 1 else prompts:
            lines.append(f"▸ {p}")
        if len(prompts) > 1:
            lines.append("  ⋯")
            lines.append(f"▸ {prompts[-1]}")
    else:
        lines.append("[dim](no user prompts recorded)[/dim]")
    return "\n".join(lines)


class ResumePickerScreen(ModalScreen[str | None]):
    """Pick a session id to resume. Returns None on Esc.

    Only *resumable* sessions dismiss with their id; selecting an
    unavailable row just shows its reason in the detail pane and stays open.
    Previews are read lazily (via the injected preview_fn) when a row is
    highlighted, so opening the picker never reads any transcript body.
    """

    CSS = """
    ResumePickerScreen { align: center middle; }
    #resume-box {
        width: 110; max-height: 26; padding: 1 2;
        background: $background; border: thick $primary;
    }
    #resume-title { color: $primary; padding-bottom: 1; }
    #resume-body { height: auto; }
    #resume-list { width: 46; height: auto; max-height: 20; }
    #resume-detail { width: 1fr; padding: 0 0 0 2; }
    #resume-help { padding-top: 1; color: $text-muted; }
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def __init__(
        self,
        metas: list[SessionMeta],
        *,
        scope_label: str,
        preview_fn: PreviewFn,
        unavailable: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self._metas = metas
        self._scope_label = scope_label
        self._preview_fn = preview_fn
        self._unavailable = unavailable or {}
        self._by_id = {m.session_id: m for m in metas}

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-box"):
            yield Static(
                f"resume a session — {self._scope_label} ({len(self._metas)})",
                id="resume-title",
            )
            with Horizontal(id="resume-body"):
                opts = [
                    Option(render_row(m, self._unavailable.get(m.session_id)),
                           id=m.session_id)
                    for m in self._metas
                ]
                yield OptionList(*opts, id="resume-list")
                yield Static(self._empty_detail(), id="resume-detail")
            yield Static("↑↓ to browse · enter to resume · esc to cancel",
                         id="resume-help")

    def _empty_detail(self) -> str:
        if not self._metas:
            return f"[dim](no sessions to resume in {self._scope_label})[/dim]"
        return "[dim]highlight a session to preview it[/dim]"

    def _detail_widget(self) -> Static:
        return self.query_one("#resume-detail", Static)

    def _show_detail_for(self, sid: str) -> None:
        m = self._by_id.get(sid)
        if m is None:
            return
        try:
            prompts = self._preview_fn(sid)
        except Exception as e:  # noqa: BLE001 — a bad preview must not crash the picker
            self._detail_widget().update(
                f"[b]session[/b]  {sid}\n[dim](preview unavailable: "
                f"{type(e).__name__}: {e})[/dim]"
            )
            return
        self._detail_widget().update(
            format_detail(m, prompts, self._unavailable.get(sid))
        )

    def on_option_list_option_highlighted(
        self, ev: OptionList.OptionHighlighted
    ) -> None:
        if ev.option is not None and ev.option.id is not None:
            self._show_detail_for(str(ev.option.id))

    def on_option_list_option_selected(
        self, ev: OptionList.OptionSelected
    ) -> None:
        sid = str(ev.option.id)
        reason = self._unavailable.get(sid)
        if reason:
            # Refuse to resume; keep the picker open and surface the reason.
            self._show_detail_for(sid)
            self.app.bell()
            return
        self.dismiss(sid)

    def action_cancel(self) -> None:
        self.dismiss(None)
