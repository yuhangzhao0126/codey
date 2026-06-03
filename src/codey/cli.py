"""REPL for the agent loop, with searchable slash commands."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

from .agent import (
    Agent,
    AssistantTextDelta,
    ToolCallRequested,
    ToolResult,
    TurnCompleted,
)
from .config import ConfigFile
from .prompt import build_system_prompt
from .tools import build_default_registry


# ---------- command registry ----------

@dataclass
class Command:
    name: str               # without leading slash
    help: str
    handler: Callable[["ReplContext", str], Awaitable[bool]]
    # handler returns True to keep looping, False to exit


@dataclass
class ReplContext:
    cfg: ConfigFile
    agent: Agent
    commands: dict[str, Command]


async def _cmd_exit(ctx: ReplContext, _arg: str) -> bool:
    return False


async def _cmd_help(ctx: ReplContext, _arg: str) -> bool:
    width = max(len(c.name) for c in ctx.commands.values())
    print("commands:")
    for c in sorted(ctx.commands.values(), key=lambda c: c.name):
        print(f"  /{c.name:<{width}}  {c.help}")
    print()
    return True


async def _cmd_reset(ctx: ReplContext, _arg: str) -> bool:
    ctx.agent.reset()
    print("(history cleared)\n")
    return True


async def _cmd_model(ctx: ReplContext, _arg: str) -> bool:
    p = ctx.agent.profile
    print(f"profile : {p.name}")
    print(f"model   : {p.model}")
    print(f"base_url: {p.base_url}\n")
    return True


async def _cmd_profiles(ctx: ReplContext, _arg: str) -> bool:
    active = ctx.agent.profile.name
    print("profiles:")
    for name in sorted(ctx.cfg.profiles):
        p = ctx.cfg.profiles[name]
        mark = "*" if name == active else " "
        print(f"  {mark} {name:<20} {p.model:<25} {p.base_url}")
    print()
    return True


async def _cmd_profile(ctx: ReplContext, arg: str) -> bool:
    target = arg.strip() or await _pick_profile(ctx)
    if not target:
        return True
    try:
        new_profile = ctx.cfg.resolve(target)
    except RuntimeError as e:
        print(f"({e})\n")
        return True
    await ctx.agent.swap_profile(new_profile)
    print(f"(switched to {new_profile.name}: {new_profile.model} @ {new_profile.base_url})\n")
    return True


async def _pick_profile(ctx: ReplContext) -> str | None:
    """Inline arrow-key picker using a prompt_toolkit completion menu.

    Opens a prompt with the completion menu forced open so ↑/↓ navigate
    immediately. Enter accepts the highlighted item.
    """
    names = sorted(ctx.cfg.profiles)
    active = ctx.agent.profile.name

    meta = {
        name: f"{ctx.cfg.profiles[name].model} @ {ctx.cfg.profiles[name].base_url}"
              + ("  (active)" if name == active else "")
        for name in names
    }
    completer = WordCompleter(names, meta_dict=meta, sentence=True)

    bindings = KeyBindings()

    @bindings.add("c-c")
    @bindings.add("escape", eager=True)
    def _cancel(event):
        event.app.exit(result=None)

    # Pop the menu open as soon as the prompt appears.
    def _pre_run() -> None:
        get_app().current_buffer.start_completion(select_first=True)

    try:
        with patch_stdout():
            choice = await PromptSession().prompt_async(
                "profile > ",
                completer=completer,
                complete_while_typing=True,
                key_bindings=bindings,
                pre_run=_pre_run,
            )
    except (EOFError, KeyboardInterrupt):
        return None
    if choice is None:
        return None
    choice = choice.strip()
    return choice or None


def _build_commands() -> dict[str, Command]:
    cmds = [
        Command("exit",     "quit codey",                                  _cmd_exit),
        Command("help",     "show this help",                              _cmd_help),
        Command("reset",    "clear chat history (keeps system prompt)",    _cmd_reset),
        Command("model",    "show the active model / profile / base_url",  _cmd_model),
        Command("profiles", "list available profiles",                     _cmd_profiles),
        Command("profile",  "switch profile: /profile [name]",             _cmd_profile),
    ]
    return {c.name: c for c in cmds}


# ---------- searchable substring completer ----------

class SlashCompleter(Completer):
    """Show a dropdown of slash commands whose name contains the typed substring.

    Only triggers when the input begins with `/` and the user is typing the
    command word (no space yet). Once they type a space (args), we stop completing.
    """

    def __init__(self, commands: dict[str, Command]):
        self.commands = commands

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " in text:
            return  # past the command name; user is typing args

        query = text[1:].lower()  # strip leading '/'
        for name, cmd in sorted(self.commands.items()):
            if query in name.lower():
                yield Completion(
                    text=f"/{name}",
                    start_position=-len(text),  # replace the whole `/xyz`
                    display=f"/{name}",
                    display_meta=cmd.help,
                )


# ---------- dispatch ----------

def _resolve_command(commands: dict[str, Command], typed: str) -> Command | list[str] | None:
    """Return the matched Command, or a list of candidate names if ambiguous, or None."""
    if typed in commands:
        return commands[typed]  # exact match wins even if it's a substring of others
    matches = [name for name in commands if typed.lower() in name.lower()]
    if len(matches) == 1:
        return commands[matches[0]]
    if len(matches) > 1:
        return matches
    return None


async def _handle_slash(ctx: ReplContext, line: str) -> bool:
    body = line[1:]  # strip '/'
    name, _, arg = body.partition(" ")
    resolved = _resolve_command(ctx.commands, name)

    if resolved is None:
        print(f"(unknown command: /{name} — try /help)\n")
        return True
    if isinstance(resolved, list):
        print(f"(ambiguous: matches {', '.join('/' + m for m in resolved)})\n")
        return True
    return await resolved.handler(ctx, arg)


# ---------- main loop ----------

async def _run(profile_arg: str | None) -> None:
    cfg = ConfigFile.load()
    profile = cfg.resolve(profile_arg)

    # Approval prompt for non-allowlisted bash commands.
    approval_session: PromptSession[str] = PromptSession()

    async def approve_bash(command: str) -> bool:
        print(f"\n  ⚠  agent wants to run:\n     $ {command}")
        try:
            with patch_stdout():
                ans = (await approval_session.prompt_async("  allow? [y/N] ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in ("y", "yes")

    tools = build_default_registry(approve=approve_bash)
    agent = Agent(
        profile=profile,
        system_prompt=build_system_prompt(),
        tools=tools,
    )
    commands = _build_commands()
    ctx = ReplContext(cfg=cfg, agent=agent, commands=commands)

    session: PromptSession[str] = PromptSession(
        completer=SlashCompleter(commands),
        complete_while_typing=True,
    )

    print(f"codey — profile: {profile.name} | model: {profile.model} @ {profile.base_url}")
    print("Type /help for commands.\n")

    try:
        while True:
            try:
                with patch_stdout():
                    user_input = (await session.prompt_async("you > ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return

            if not user_input:
                continue

            if user_input.startswith("/"):
                keep_going = await _handle_slash(ctx, user_input)
                if not keep_going:
                    return
                continue

            print("codey > ", end="", flush=True)
            try:
                async for event in agent.run(user_input):
                    if isinstance(event, AssistantTextDelta):
                        print(event.text, end="", flush=True)
                    elif isinstance(event, ToolCallRequested):
                        print(f"\n  → tool {event.name}({event.arguments})", flush=True)
                    elif isinstance(event, ToolResult):
                        tag = "ok" if event.ok else "err"
                        print(f"  ← {event.name} [{tag}] {event.content}", flush=True)
                    elif isinstance(event, TurnCompleted):
                        if event.reason == "error":
                            print(f"\n[error] {event.error}", file=sys.stderr)
                        elif event.reason == "cancelled":
                            print("\n[cancelled]", file=sys.stderr)
            except KeyboardInterrupt:
                print("\n[interrupted]", file=sys.stderr)
            print("\n")
    finally:
        await agent.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="codey", description="codey — a coding agent")
    parser.add_argument(
        "--profile", "-p",
        help="profile name from ~/.config/codey/config.toml (overrides $CODEY_PROFILE)",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="launch the Textual full-screen UI instead of the line REPL",
    )
    args = parser.parse_args()
    if args.tui:
        from .tui import run as run_tui  # import lazily so the REPL stays light
        run_tui(args.profile)
    else:
        asyncio.run(_run(args.profile))


if __name__ == "__main__":
    main()
