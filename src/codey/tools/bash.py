"""Bash tool: run shell commands on the local machine.

Approval flows through a PermissionEngine. If no engine is provided, the
tool falls back to a permissive in-memory engine (used by tests and the
no-config bootstrap path).

Execution is via /bin/bash -c with a 60s timeout. stdout+stderr are merged
and capped at 10_000 chars so we don't blow the context window.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..permissions import Allow, Ask, Deny, PermissionEngine, Rule, suggest_pattern

# An approve callback receives the prompt context and returns a Verdict.
# See cli.py / tui.py for actual implementations. None means "auto-allow".
ApproveCtx = dict[str, Any]   # {tool, command, reason, suggested_pattern}
ApproveFn = Callable[[ApproveCtx], "Verdict | Awaitable[Verdict]"]


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    # When 'remember' is set, the host should append a rule to the engine.
    remember: bool = False
    remember_action: str = "allow"        # "allow" | "deny"
    remember_pattern: str = ""
    remember_scope: str = "project"       # "project" | "user"


MAX_OUTPUT_CHARS = 10_000
DEFAULT_TIMEOUT_SECONDS = 60


@dataclass
class BashTool:
    engine: PermissionEngine = None  # type: ignore[assignment]
    approve: ApproveFn | None = None
    timeout: int = DEFAULT_TIMEOUT_SECONDS

    name: str = "bash"
    description: str = (
        "Execute a shell command on the user's local machine via /bin/bash. "
        "Returns merged stdout+stderr and the exit code. "
        "Permission depends on the active permission rules and mode; some "
        "commands run immediately, some require user approval, some are denied. "
        "Use this for inspecting files, running tests, running git, etc. "
        "Commands time out after 60 seconds."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.engine is None:
            self.engine = PermissionEngine()  # permissive default
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

        decision = self.engine.check("bash", command)
        if isinstance(decision, Deny):
            return f"error: blocked by permission rule: {decision.reason}"
        if isinstance(decision, Ask):
            verdict = await self._ask({
                "tool": "bash",
                "command": command,
                "reason": decision.reason,
                "suggested_pattern": suggest_pattern("bash", command),
            })
            if not verdict.allowed:
                return f"error: user denied permission to run: {command}"
            if verdict.remember and verdict.remember_pattern:
                rule = Rule(
                    tool="bash",
                    pattern=verdict.remember_pattern,
                    action=verdict.remember_action,  # type: ignore[arg-type]
                    reason="user-added via approval prompt",
                )
                if verdict.remember_scope == "user":
                    self.engine.append_user_rule(rule)
                else:
                    self.engine.append_project_rule(rule)

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

    async def _ask(self, ctx: ApproveCtx) -> Verdict:
        if self.approve is None:
            return Verdict(allowed=True)
        result = self.approve(ctx)
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, Verdict) else Verdict(allowed=bool(result))
