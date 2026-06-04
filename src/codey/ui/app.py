"""Textual TUI for codey.

Layout (geek-clear, minimal):

  ┌────────────────────────────────────────────────────────────┐
  │ codey · profile · model · base_url                  header │
  ├────────────────────────────────────────────────────────────┤
  │                                                            │
  │   you  › what's the python version?                        │
  │     → bash(command='python --version')                     │
  │     ← bash [ok]   Python 3.14.5                            │
  │   codey› 3.14.5                                            │
  │                                                            │
  │                                              (transcript)  │
  ├────────────────────────────────────────────────────────────┤
  │   ┌─────────────────────┐                                  │
  │   │ /profile  switch …  │  ← dropdown appears when you     │
  │   │ /profiles list pr…  │    type a slash; ↑/↓ Enter Esc   │
  │   └─────────────────────┘                                  │
  │ > /pro█                                                    │  (input)
  ├────────────────────────────────────────────────────────────┤
  │ ctrl+c quit · ctrl+r reset · ctrl+p profile           foot │
  └────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, OptionList, RichLog
from textual.worker import Worker

from ..agent import Agent
from ..config import ConfigFile
from ..core.session import Session
from ..hooks import HookRegistry
from ..hooks.builtin import build_default_hooks
from ..permissions import MODE_DESCRIPTIONS, Mode, PermissionEngine, Verdict
from ..prompt import build_system_prompt
from ..tools import build_default_registry
from . import renderers, streaming
from .modals.approval import ApprovalScreen
from .modals.mode_picker import ModePickerScreen
from .modals.profile_picker import ProfilePickerScreen
from .modals.remember import RememberScreen
from .renderers import UISinks
from .slash_commands import SlashCommand, build_slash_commands, handle_slash
from .slash_suggest import SlashSuggest


class CodeyApp(App[None]):
    """A minimal terminal UI for the codey agent loop."""

    CSS = """
    Screen { background: $background; layers: base overlay; }

    #transcript {
        background: $background;
        padding: 0 1;
        scrollbar-background: $background;
        scrollbar-color: $primary 40%;
    }

    #input-row {
        height: 3;
        padding: 0 1;
        background: $background;
        border-top: solid $primary 30%;
    }

    Input {
        background: $background;
        border: none;
    }

    Header { background: $primary 30%; }
    Footer { background: $primary 30%; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit",     "quit",    priority=True),
        Binding("ctrl+d", "quit",     "quit",    show=False, priority=True),
        Binding("ctrl+r", "reset",    "reset history"),
        Binding("ctrl+p", "profile",  "switch profile", priority=True),
    ]

    # Disable Textual's built-in ctrl+p command palette; we use ctrl+p for our picker.
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, profile_arg: str | None) -> None:
        super().__init__()
        self.profile_arg = profile_arg
        self.session: Session  # set in on_mount
        self._busy = False
        self._turn_worker: Worker | None = None  # current in-flight model turn
        self._assistant_buf = ""
        self.slash_commands: dict[str, SlashCommand] = build_slash_commands()

    # -- back-compat property proxies onto Session --
    # Several tests (and slash-command handlers) read these directly off the
    # app object. Forward to self.session so the names keep working.

    @property
    def agent(self) -> Agent:
        return self.session.agent

    @property
    def engine(self) -> PermissionEngine:
        return self.session.engine

    @property
    def hooks(self) -> HookRegistry:
        return self.session.hooks

    @property
    def cfg(self) -> ConfigFile:
        return self.session.cfg

    @property
    def workspace(self):
        return self.session.workspace

    IDLE_PLACEHOLDER = "message codey — / for commands"
    BUSY_PLACEHOLDER = "codey is working… (esc to cancel)"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="transcript", wrap=True, markup=True, highlight=False, auto_scroll=True)
        yield SlashSuggest(id="slash-suggest")
        yield Input(placeholder=self.IDLE_PLACEHOLDER, id="input-row")
        yield Footer()

    async def on_mount(self) -> None:
        # Build the default hook set wired to TUI sinks.
        def transcript_writer(style: str, text: str) -> None:
            color = {"tool": "yellow", "ok": "green", "err": "red"}.get(style, "white")
            self.transcript.write(f"  [bold {color}]{text}[/]")

        def meta_writer(text: str) -> None:
            self._log_meta(text)

        def todo_line_writer(text: str) -> None:
            self.transcript.write(text)
        todo_writer = renderers.make_tui_todo_writer(todo_line_writer)

        sinks = UISinks(
            transcript_writer=transcript_writer,
            meta_writer=meta_writer,
            approve=self._approve_tool,
            todo_writer=todo_writer,
        )
        self.session = Session.build(
            profile_arg=self.profile_arg,
            ui_sinks=sinks,
        )

        self._refresh_title()
        self._log_meta("codey ready · type / for commands · ctrl+c to quit")
        self._log_meta(f"workspace: {self.workspace}")
        self._log_meta(f"permission mode: {self.engine.mode.value}"
                       + ("  ⚠" if self.engine.mode == Mode.YOLO else ""))
        self.query_one(Input).focus()

    async def on_unmount(self) -> None:
        if hasattr(self, "session"):
            await self.session.aclose()

    # -- approval hook handed to all permission-gated tools --

    async def _approve_tool(self, ctx_dict: dict) -> Verdict:
        # The agent loop already runs inside a worker (see on_input_submitted),
        # so push_screen_wait is safe here.
        ans = await self.push_screen_wait(ApprovalScreen(
            tool=ctx_dict["tool"],
            command=ctx_dict["command"],
            reason=ctx_dict.get("reason", ""),
        ))
        if ans in (None, "cancel", "n"):
            return Verdict(allowed=False)
        if ans == "y":
            return Verdict(allowed=True)
        # 'a' or 'd' — ask for pattern + scope before persisting.
        action = "allow" if ans == "a" else "deny"
        suggested = ctx_dict.get("suggested_pattern") or "*"
        result = await self.push_screen_wait(RememberScreen(action, suggested))
        if not result:
            # User backed out of the remember step; fall back to one-shot.
            return Verdict(allowed=(action == "allow"))
        pattern, scope = result
        self._log_meta(f"(rule added: {action} {ctx_dict['tool']}:{pattern} → {scope})")
        return Verdict(
            allowed=(action == "allow"),
            remember=True,
            remember_action=action,
            remember_pattern=pattern,
            remember_scope=scope,
        )

    # -- slash command handlers (called by build_slash_commands closures) --

    async def _cmd_exit(self) -> None:
        self.exit()

    async def _cmd_model(self) -> None:
        # Reads from self.agent.profile, which is updated live by swap_profile,
        # so this always reflects the current runtime.
        p = self.agent.profile
        self._log_meta(f"profile  : {p.name}")
        self._log_meta(f"model    : {p.model}")
        self._log_meta(f"base_url : {p.base_url}")
        self._log_meta(f"workspace: {self.workspace}")

    async def _cmd_permission(self, arg: str) -> None:
        arg = arg.strip()
        eng = self.engine
        if not arg:
            # Bare `/permission` opens the mode picker.
            self._open_mode_picker()
            return
        sub, _, rest = arg.partition(" ")
        sub = sub.lower()
        if sub == "mode":
            name = rest.strip().lower()
            if not name:
                # `/permission mode` (no arg) also opens the picker.
                self._open_mode_picker()
                return
            try:
                new_mode = Mode(name)
            except ValueError:
                self._log_error(
                    f"unknown mode: {name!r}; choose: "
                    + ", ".join(m.value for m in Mode)
                )
                return
            self._apply_mode(new_mode)
            return
        if sub == "status":
            self._log_meta(f"mode    : {eng.mode.value}")
            self._log_meta(f"user    : {len(eng.user_rules)} rule(s)")
            self._log_meta(f"project : {len(eng.project_rules)} rule(s)")
            return
        if sub == "list":
            if not eng.user_rules and not eng.project_rules:
                self._log_meta("(no user or project rules)")
                return
            for label, rules in (("project", eng.project_rules), ("user", eng.user_rules)):
                if rules:
                    self._log_meta(f"[{label}]")
                    for i, r in enumerate(rules):
                        reason = f"  ({r.reason})" if r.reason else ""
                        self._log_meta(
                            f"  {i:>2}  {r.action:<5} {r.tool:<10} {r.pattern}{reason}"
                        )
            return
        self._log_error(f"unknown subcommand: /permission {sub}")

    async def _cmd_hooks(self, arg: str) -> None:
        """Subcommands: (none) → list; enable <name>; disable <name>."""
        arg = arg.strip()
        if not arg:
            hooks = self.hooks.list()
            if not hooks:
                self._log_meta("(no hooks registered)")
                return
            for h in hooks:
                mark = "✓" if h.enabled else "·"
                self._log_meta(f"  {mark} {h.event.value:<18} {h.name}")
            return
        sub, _, rest = arg.partition(" ")
        sub = sub.lower()
        target = rest.strip()
        if sub in ("enable", "disable") and target:
            if sub == "disable" and target == "permission":
                self._log_meta("(⚠ disabling the permission hook lets the model "
                               "run any tool unattended)")
            ok = self.hooks.enable(target) if sub == "enable" else self.hooks.disable(target)
            if ok:
                self._log_meta(f"(hook {target!r} {sub}d)")
            else:
                self._log_error(f"unknown hook: {target!r}")
            return
        self._log_error(f"unknown subcommand: /hooks {sub}")

    def _open_mode_picker(self) -> None:
        """Open the mode picker in a worker (push_screen_wait needs one)."""
        async def _pick() -> None:
            chosen = await self.push_screen_wait(ModePickerScreen(self.engine.mode))
            if chosen is None:
                return
            try:
                new_mode = Mode(chosen)
            except ValueError:
                self._log_error(f"unknown mode: {chosen!r}")
                return
            self._apply_mode(new_mode)
        self.run_worker(_pick(), exclusive=True, group="mode-picker")

    def _apply_mode(self, new_mode: Mode) -> None:
        self.engine.save_mode(new_mode)
        self._refresh_title()
        warn = "  ⚠" if new_mode == Mode.YOLO else ""
        self._log_meta(f"(permission mode → {new_mode.value}{warn})")

    async def _cmd_help(self) -> None:
        width = max(len(c.name) for c in self.slash_commands.values())
        for c in sorted(self.slash_commands.values(), key=lambda c: c.name):
            self._log_meta(f"  /{c.name:<{width}}  {c.help}")

    async def _cmd_reset(self) -> None:
        self.agent.reset()
        self._log_meta("(history cleared)")

    async def _cmd_profiles_list(self) -> None:
        active = self.agent.profile.name
        for name in sorted(self.cfg.profiles):
            p = self.cfg.profiles[name]
            mark = "*" if name == active else " "
            self._log_meta(f"  {mark} {name:<20} {p.model:<25} {p.base_url}")

    async def _cmd_profile_switch(self, arg: str) -> None:
        arg = arg.strip()
        if arg:
            await self._switch_profile(arg)
        else:
            # push_screen_wait requires a worker context — launch one.
            self._open_profile_picker()

    def _open_profile_picker(self) -> None:
        """Schedule the picker modal as a worker (required by push_screen_wait)."""
        async def _pick() -> None:
            chosen = await self.push_screen_wait(
                ProfilePickerScreen(self.cfg, self.agent.profile.name)
            )
            if chosen:
                await self._switch_profile(chosen)
        self.run_worker(_pick(), exclusive=True, group="profile-picker")

    async def _switch_profile(self, name: str) -> None:
        try:
            new_profile = await self.session.swap_profile(name)
        except RuntimeError as e:
            self._log_error(str(e))
            return
        self._refresh_title()
        self._log_meta(f"(switched to {new_profile.name}: {new_profile.model})")

    # -- rendering helper proxies (kept on the app for the few callers that
    # use them, including the streaming module). Real renderers live in
    # renderers.py.

    @property
    def transcript(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    def _refresh_title(self) -> None:
        p = self.agent.profile
        self.title = "codey"
        mode_str = self.engine.mode.value.upper()
        # Show workspace basename so the header stays short; full path is in
        # the transcript at startup and via /model.
        ws = self.workspace.name or str(self.workspace)
        self.sub_title = f"{p.name} · {p.model} · cwd: {ws} · mode: {mode_str}"

    def _log_meta(self, text: str) -> None:
        renderers.log_meta(self.transcript, text)

    def _log_user(self, text: str) -> None:
        renderers.log_user(self.transcript, text)

    def _log_assistant(self, text: str) -> None:
        renderers.log_assistant(self.transcript, text)

    def _log_tool_call(self, name: str, args: dict) -> None:
        renderers.log_tool_call(self.transcript, name, args)

    def _log_tool_result(self, name: str, ok: bool, content: str) -> None:
        renderers.log_tool_result(self.transcript, name, ok, content)

    def _log_error(self, text: str) -> None:
        renderers.log_error(self.transcript, text)

    # -- actions (hotkeys) --

    def action_reset(self) -> None:
        # mirror /reset
        self.agent.reset()
        self._log_meta("(history cleared)")

    async def action_profile(self) -> None:
        # ctrl+p → open the picker modal (runs in a worker so push_screen_wait works)
        self._open_profile_picker()

    # -- input handling: search-as-you-type slash dropdown --

    def on_input_changed(self, event: Input.Changed) -> None:
        text = event.value
        suggest = self.query_one(SlashSuggest)
        if text.startswith("/") and " " not in text:
            query = text[1:].lower()
            matches = [
                (name, cmd) for name, cmd in sorted(self.slash_commands.items())
                if query in name.lower()
            ]
            if matches:
                from textual.widgets.option_list import Option
                suggest.clear_options()
                for name, cmd in matches:
                    suggest.add_option(
                        Option(f"[bold]/{name}[/]  [dim]{cmd.help}[/]", id=name)
                    )
                suggest.highlighted = 0
                suggest.styles.display = "block"
                return
        suggest.styles.display = "none"
        suggest.clear_options()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User pressed Enter while the slash dropdown was focused."""
        if event.option_list.id != "slash-suggest":
            return
        name = event.option.id
        if not name:
            return
        inp = self.query_one(Input)
        # Replace the typed `/xyz` with the full command name and keep typing args.
        inp.value = f"/{name} "
        inp.cursor_position = len(inp.value)
        suggest = self.query_one(SlashSuggest)
        suggest.styles.display = "none"
        inp.focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if self._busy:
            # Don't clobber the user's typing; tell them what's going on.
            self._log_meta(
                "(busy — press esc to cancel the current turn, or wait)"
            )
            return
        event.input.clear()
        suggest = self.query_one(SlashSuggest)
        suggest.styles.display = "none"
        suggest.clear_options()
        if not text:
            return

        if text.startswith("/"):
            await handle_slash(self, text)
            return

        self._log_user(text)
        self._set_busy(True)
        # Run the model turn in a worker so push_screen_wait (used by the
        # bash-approval modal) has a worker context, AND so we can cancel it.
        self._turn_worker = self.run_worker(
            self._stream_turn_and_release(text),
            exclusive=True, group="agent-turn",
        )

    async def _stream_turn_and_release(self, user_input: str) -> None:
        try:
            await streaming.stream_turn(self, user_input)
        finally:
            self._set_busy(False)
            self._turn_worker = None

    def _set_busy(self, busy: bool) -> None:
        """Flip busy state and update the input placeholder so the user can see it."""
        self._busy = busy
        try:
            inp = self.query_one(Input)
        except Exception:  # noqa: BLE001
            return
        inp.placeholder = self.BUSY_PLACEHOLDER if busy else self.IDLE_PLACEHOLDER

    def _cancel_current_turn(self) -> bool:
        """Cancel an in-flight model turn. Returns True if a turn was cancelled."""
        if not self._busy or self._turn_worker is None:
            return False
        self._turn_worker.cancel()
        # _stream_turn_and_release's finally clause will clear _busy and emit
        # the cancellation Event from the agent loop.
        self._log_meta("(cancelling current turn…)")
        return True

    # -- key routing: arrow keys steer the dropdown when it's visible;
    #    escape cancels an in-flight turn when nothing else needs it --

    async def on_key(self, event) -> None:
        suggest = self.query_one(SlashSuggest)
        dropdown_open = suggest.styles.display != "none" and suggest.option_count > 0

        if dropdown_open:
            if event.key in ("down", "up"):
                current = suggest.highlighted or 0
                n = suggest.option_count
                suggest.highlighted = (current + (1 if event.key == "down" else -1)) % n
                event.stop()
                return
            if event.key == "enter":
                opt = suggest.get_option_at_index(suggest.highlighted or 0)
                if opt and opt.id:
                    inp = self.query_one(Input)
                    inp.value = f"/{opt.id} "
                    inp.cursor_position = len(inp.value)
                    suggest.styles.display = "none"
                    event.stop()
                return
            if event.key == "escape":
                suggest.styles.display = "none"
                event.stop()
                return

        # Dropdown isn't open. Use escape to cancel an in-flight turn.
        if event.key == "escape" and self._cancel_current_turn():
            event.stop()


def run(profile_arg: str | None) -> None:
    app = CodeyApp(profile_arg=profile_arg)
    app.run()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(prog="codey", description="codey — a coding agent")
    parser.add_argument(
        "--profile", "-p",
        help="profile name from ~/.config/codey/config.toml (overrides $CODEY_PROFILE)",
    )
    args = parser.parse_args()
    run(args.profile)
