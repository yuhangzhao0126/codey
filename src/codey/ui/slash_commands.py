"""SlashCommand registry + dispatcher for the TUI.

Pure data + dispatch — handlers are bound at construction time as closures
over the CodeyApp instance. Substring-match resolution: typing `/pro` resolves
to `/profile` if it's the unique match.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .app import CodeyApp


@dataclass
class SlashCommand:
    name: str
    help: str
    # handler is async; receives the app and the arg string (may be empty)
    handler: Callable[["CodeyApp", str], Awaitable[None]]


def build_slash_commands() -> dict[str, SlashCommand]:
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
        SlashCommand("permission", "permission mode picker; subcommands: status, list, mode <name>",
                     lambda app, arg: app._cmd_permission(arg)),
        SlashCommand("hooks",      "list / enable / disable hooks: /hooks [enable|disable <name>]",
                     lambda app, arg: app._cmd_hooks(arg)),
        SlashCommand("subs",       "show this session's sub-agents and their event timelines",
                     lambda app, _: app._cmd_subs()),
        SlashCommand("skills",     "list all available skills (package + user + project)",
                     lambda app, _: app._cmd_skills()),
        SlashCommand("compact",    "summarize history into a single message; preserves system prompt",
                     lambda app, _: app._cmd_compact()),
        SlashCommand("resume",     "resume a session from this workspace",
                     lambda app, _: app._cmd_resume()),
        SlashCommand("remember",   "save a memory entry: /remember <freeform text>",
                     lambda app, arg: app._cmd_remember(arg)),
    ]
    return {c.name: c for c in cmds}


async def handle_slash(app: "CodeyApp", line: str) -> None:
    """Dispatch a slash-prefixed user input. Substring match with disambiguation."""
    body = line[1:]
    name, _, arg = body.partition(" ")
    name = name.lower()
    cmds = app.slash_commands

    if name in cmds:  # exact wins
        await cmds[name].handler(app, arg)
        return
    matches = [n for n in cmds if name in n]
    if not matches:
        app._log_error(f"unknown command: /{name} — try /help")
        return
    if len(matches) > 1:
        app._log_error(
            f"ambiguous: matches {', '.join('/' + m for m in matches)}"
        )
        return
    await cmds[matches[0]].handler(app, arg)
