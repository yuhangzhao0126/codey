"""Bash tool: run shell commands on the local machine.

Approval policy:
- A small allowlist of read-only commands (ls, cat, git status, etc.) runs
  without prompting.
- Anything else calls the `approve(command)` hook supplied by the host.
  If approve returns False, the tool returns an error string to the model
  (the model can then choose to try a different command).
- If no `approve` hook is provided, non-allowlisted commands auto-run.
  Suitable for tests; the REPL always wires one up.

Execution is via /bin/bash -c with a 60s timeout. stdout+stderr are merged
and capped at 10_000 chars so we don't blow the context window.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

ApproveFn = Callable[[str], "bool | Awaitable[bool]"]

# First token of each command line. Conservative — everything else asks.
READONLY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "ls", "pwd", "cat", "head", "tail", "wc", "file", "stat",
        "tree", "find", "echo", "which", "whoami", "date", "uname",
        "env", "printenv", "df", "du", "ps", "uptime",
    }
)

# git subcommands that don't mutate state.
READONLY_GIT_SUBS: frozenset[str] = frozenset(
    {"status", "log", "diff", "show", "blame", "branch", "remote", "config"}
)

MAX_OUTPUT_CHARS = 10_000
DEFAULT_TIMEOUT_SECONDS = 60


def _is_readonly(command: str) -> bool:
    """Best-effort: split on the first pipe/and/semicolon, look at the first
    token of the first segment. Anything fancy => not readonly => prompt."""
    if any(op in command for op in ("|", ";", "&&", "||", ">", "<", "`", "$(")):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if not tokens:
        return False
    cmd = tokens[0]
    if cmd in READONLY_ALLOWLIST:
        return True
    if cmd == "git" and len(tokens) >= 2 and tokens[1] in READONLY_GIT_SUBS:
        return True
    return False


@dataclass
class BashTool:
    """Implements the `Tool` Protocol from codey.agent."""

    approve: ApproveFn | None = None
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    name: str = "bash"
    description: str = (
        "Execute a shell command on the user's local machine via /bin/bash. "
        "Returns merged stdout+stderr and the exit code. "
        "Read-only commands run immediately; anything else requires user approval, "
        "which the user may deny. Commands time out after 60 seconds. Use this for "
        "inspecting files, running tests, running git, etc."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute. Will be run as `bash -c <command>`.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        command = (arguments.get("command") or "").strip()
        if not command:
            return "error: empty command"

        if not _is_readonly(command):
            allowed = await self._ask(command)
            if not allowed:
                return f"error: user denied permission to run: {command}"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                executable="/bin/bash",
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"error: command timed out after {self.timeout}s"
        except Exception as e:  # noqa: BLE001
            return f"error: {type(e).__name__}: {e}"

        out = stdout.decode("utf-8", errors="replace")
        if len(out) > MAX_OUTPUT_CHARS:
            head = out[: MAX_OUTPUT_CHARS // 2]
            tail = out[-MAX_OUTPUT_CHARS // 2 :]
            out = f"{head}\n…[truncated {len(out) - MAX_OUTPUT_CHARS} chars]…\n{tail}"

        return f"exit={proc.returncode}\n{out}"

    async def _ask(self, command: str) -> bool:
        if self.approve is None:
            return True
        result = self.approve(command)
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)
