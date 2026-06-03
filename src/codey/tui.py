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

Approvals for non-readonly bash commands open a small modal with
`y` / `n` bindings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option
from textual.worker import Worker

from .agent import (
    Agent,
    AssistantMessageCompleted,
    AssistantTextDelta,
    RoundStarted,
    ToolCallRequested,
    ToolResult,
    TurnCompleted,
    TurnStarted,
)
from .config import ConfigFile, Profile
from .permissions import Mode, PermissionEngine, Rule
from .prompt import build_system_prompt
from .tools import Verdict, build_default_registry


# ---------- slash command registry ----------

@dataclass
class SlashCommand:
    name: str
    help: str
    # handler is async; receives the app and the arg string (may be empty)
    handler: Callable[["CodeyApp", str], Awaitable[None]]


# ---------- approval modal ----------

class ApprovalScreen(ModalScreen[str]):
    """4-option modal: returns 'y' (allow once), 'a' (always allow),
    'n' (deny once), 'd' (always deny), or 'cancel' (esc)."""

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
    #approval-title  { color: $warning; padding-bottom: 1; }
    #approval-cmd    { color: $text;    padding-bottom: 1; }
    #approval-reason { color: $text-muted; padding-bottom: 1; }
    #approval-help   { color: $text-muted; }
    """

    def __init__(self, *, tool: str, command: str, reason: str = "") -> None:
        super().__init__()
        self.tool = tool
        self.command = command
        self.reason = reason

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-box"):
            yield Static(f"⚠  agent wants to use tool [b]{self.tool}[/]", id="approval-title")
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


class RememberScreen(ModalScreen[tuple[str, str] | None]):
    """Asks the user for a pattern and a scope (project/user) before saving
    an allow/deny rule. Returns (pattern, scope) or None on cancel."""

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


# ---------- profile picker modal ----------

class ProfilePickerScreen(ModalScreen[str | None]):
    """Inline list with arrow keys + Enter. Returns chosen profile name or None."""

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


# ---------- slash-command dropdown ----------

class SlashSuggest(OptionList):
    """Floating list of matching slash commands, anchored above the input.

    Visible only while the user is typing a slash command (line starts with '/'
    and there is no space yet — once they start typing args, we hide).
    """

    DEFAULT_CSS = """
    SlashSuggest {
        layer: overlay;
        dock: bottom;
        offset: 0 -3;            /* sit just above the input row */
        height: auto;
        max-height: 8;
        width: 60;
        margin: 0 1;
        background: $panel;
        border: round $primary;
        display: none;
    }
    SlashSuggest:focus { border: round $accent; }
    """


# ---------- main app ----------

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
        self.cfg = ConfigFile.load()
        self.engine: PermissionEngine = PermissionEngine.load()
        self.agent: Agent  # set in on_mount
        self._busy = False
        self._turn_worker: Worker | None = None  # current in-flight model turn
        self._assistant_buf = ""
        self.slash_commands: dict[str, SlashCommand] = self._build_slash_commands()

    IDLE_PLACEHOLDER = "message codey — / for commands"
    BUSY_PLACEHOLDER = "codey is working… (esc to cancel)"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="transcript", wrap=True, markup=True, highlight=False, auto_scroll=True)
        yield SlashSuggest(id="slash-suggest")
        yield Input(placeholder=self.IDLE_PLACEHOLDER, id="input-row")
        yield Footer()

    async def on_mount(self) -> None:
        profile = self.cfg.resolve(self.profile_arg)
        self.agent = Agent(
            profile=profile,
            system_prompt=build_system_prompt(),
            tools=build_default_registry(engine=self.engine, approve=self._approve_tool),
        )
        self._refresh_title()
        self._log_meta("codey ready · type / for commands · ctrl+c to quit")
        self._log_meta(f"permission mode: {self.engine.mode.value}"
                       + ("  ⚠" if self.engine.mode == Mode.YOLO else ""))
        self.query_one(Input).focus()

    async def on_unmount(self) -> None:
        if hasattr(self, "agent"):
            await self.agent.aclose()

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

    # -- slash command registry --

    def _build_slash_commands(self) -> dict[str, SlashCommand]:
        cmds = [
            SlashCommand("exit",       "quit codey",
                         lambda app, _: app._cmd_exit()),
            SlashCommand("help",       "show this help",
                         lambda app, _: app._cmd_help()),
            SlashCommand("reset",      "clear chat history (keeps system prompt)",
                         lambda app, _: app._cmd_reset()),
            SlashCommand("model",      "show the active model / profile / base_url",
                         lambda app, _: app._cmd_model()),
            SlashCommand("profiles",   "list available profiles",
                         lambda app, _: app._cmd_profiles_list()),
            SlashCommand("profile",    "switch profile: /profile [name]",
                         lambda app, arg: app._cmd_profile_switch(arg)),
            SlashCommand("permission", "permission mode + rules: /permission [mode <name>|list]",
                         lambda app, arg: app._cmd_permission(arg)),
        ]
        return {c.name: c for c in cmds}

    async def _cmd_exit(self) -> None:
        self.exit()

    async def _cmd_model(self) -> None:
        # Reads from self.agent.profile, which is updated live by swap_profile,
        # so this always reflects the current runtime.
        p = self.agent.profile
        self._log_meta(f"profile : {p.name}")
        self._log_meta(f"model   : {p.model}")
        self._log_meta(f"base_url: {p.base_url}")

    async def _cmd_permission(self, arg: str) -> None:
        arg = arg.strip()
        eng = self.engine
        if not arg:
            self._log_meta(f"mode    : {eng.mode.value}")
            self._log_meta(f"user    : {len(eng.user_rules)} rule(s)")
            self._log_meta(f"project : {len(eng.project_rules)} rule(s)")
            self._log_meta("subcommands: mode <safe|paranoid|read-only|yolo>, list")
            return
        sub, _, rest = arg.partition(" ")
        sub = sub.lower()
        if sub == "mode":
            name = rest.strip().lower()
            try:
                new_mode = Mode(name)
            except ValueError:
                self._log_error(
                    f"unknown mode: {name!r}; choose: "
                    + ", ".join(m.value for m in Mode)
                )
                return
            eng.save_mode(new_mode)
            self._refresh_title()
            warn = "  ⚠" if new_mode == Mode.YOLO else ""
            self._log_meta(f"(permission mode → {new_mode.value}{warn})")
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
            new_profile = self.cfg.resolve(name)
        except RuntimeError as e:
            self._log_error(str(e))
            return
        await self.agent.swap_profile(new_profile)
        self._refresh_title()
        self._log_meta(f"(switched to {new_profile.name}: {new_profile.model})")

    # -- rendering helpers --

    @property
    def transcript(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    def _refresh_title(self) -> None:
        p = self.agent.profile
        self.title = "codey"
        mode_str = self.engine.mode.value.upper()
        self.sub_title = f"{p.name} · {p.model} · mode: {mode_str}"

    def _log_meta(self, text: str) -> None:
        self.transcript.write(f"[dim]{text}[/]")

    def _log_user(self, text: str) -> None:
        self.transcript.write(f"[bold cyan]you  ›[/] {text}")

    def _log_assistant(self, text: str) -> None:
        self.transcript.write(f"[bold magenta]codey›[/] {text}")

    def _log_tool_call(self, name: str, args: dict) -> None:
        rendered_args = ", ".join(f"{k}={v!r}" for k, v in args.items())
        self.transcript.write(f"  [bold yellow]→ {name}[/]([dim]{rendered_args}[/])")

    def _log_tool_result(self, name: str, ok: bool, content: str) -> None:
        tag_color = "green" if ok else "red"
        tag = "ok" if ok else "err"
        body = "\n".join(f"    [dim]{line}[/]" for line in content.splitlines() or [""])
        self.transcript.write(f"  [bold {tag_color}]← {name} [{tag}][/]\n{body}")

    def _log_error(self, text: str) -> None:
        self.transcript.write(f"[bold red]✗ {text}[/]")

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
            await self._handle_slash(text)
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
            await self._stream_turn(user_input)
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

    # -- slash command dispatch with substring match --

    async def _handle_slash(self, line: str) -> None:
        body = line[1:]
        name, _, arg = body.partition(" ")
        name = name.lower()

        if name in self.slash_commands:  # exact wins
            await self.slash_commands[name].handler(self, arg)
            return
        matches = [n for n in self.slash_commands if name in n]
        if not matches:
            self._log_error(f"unknown command: /{name} — try /help")
            return
        if len(matches) > 1:
            self._log_error(
                f"ambiguous: matches {', '.join('/' + m for m in matches)}"
            )
            return
        await self.slash_commands[matches[0]].handler(self, arg)

    # -- turn streaming --

    async def _stream_turn(self, user_input: str) -> None:
        self._assistant_buf = ""
        async for ev in self.agent.run(user_input):
            if isinstance(ev, TurnStarted):
                pass
            elif isinstance(ev, RoundStarted):
                pass
            elif isinstance(ev, AssistantTextDelta):
                self._assistant_buf += ev.text
            elif isinstance(ev, ToolCallRequested):
                if self._assistant_buf.strip():
                    self._log_assistant(self._assistant_buf.strip())
                    self._assistant_buf = ""
                self._log_tool_call(ev.name, ev.arguments)
            elif isinstance(ev, ToolResult):
                self._log_tool_result(ev.name, ev.ok, ev.content)
            elif isinstance(ev, AssistantMessageCompleted):
                self._assistant_buf = ev.text
            elif isinstance(ev, TurnCompleted):
                if self._assistant_buf.strip():
                    self._log_assistant(self._assistant_buf.strip())
                self._assistant_buf = ""
                if ev.reason == "error":
                    self._log_error(ev.error or "unknown error")
                elif ev.reason == "cancelled":
                    self._log_meta("(cancelled)")


def run(profile_arg: str | None) -> None:
    app = CodeyApp(profile_arg=profile_arg)
    app.run()
