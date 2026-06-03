"""Bash tool: run shell commands on the local machine.

Permission gating is handled by the PreToolUse permission hook (see
codey.builtin_hooks.permission). This module is now just the execution layer.

Execution is via /bin/bash -c with a 60s timeout. stdout+stderr are merged
and capped at 10_000 chars so we don't blow the context window.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# Re-exported for backward compatibility with tests / external callers that
# imported Verdict from this module.
from ..permissions import Verdict  # noqa: F401

# An approve callback receives the prompt context and returns a Verdict.
# Lives here for compat; the canonical type is hooks-flavored now.
ApproveCtx = dict[str, Any]
ApproveFn = Callable[[ApproveCtx], "Verdict | Awaitable[Verdict]"]


MAX_OUTPUT_CHARS = 10_000
DEFAULT_TIMEOUT_SECONDS = 60


@dataclass
class BashTool:
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    name: str = "bash"
    description: str = (
        "Execute a shell command on the user's local machine via /bin/bash. "
        "Returns merged stdout+stderr and the exit code. "
        "Subject to the active permission rules — some commands run "
        "immediately, some require user approval, some are denied. "
        "Use this for inspecting files, running tests, running git, etc. "
        "Commands time out after 60 seconds."
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
