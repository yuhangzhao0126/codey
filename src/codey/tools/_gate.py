"""Shared permission gating for tools.

Every tool that wants to be permission-checked calls `gate()` at the top of
its `run()`. Returns None when the tool may proceed; otherwise returns an
error string the tool should hand back to the model.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..permissions import Ask, Deny, PermissionEngine, Rule, suggest_pattern
from .bash import ApproveFn, Verdict


async def gate(
    *,
    engine: PermissionEngine,
    approve: ApproveFn | None,
    tool: str,
    arg_str: str,
    summary: str,
) -> str | None:
    """Consult the engine. Return an error string if the call is blocked,
    or None if it may proceed.

    `arg_str` is the canonical arg used for rule matching (the path for file
    tools; the command string for bash). `summary` is the human-facing
    description shown in the approval modal."""
    decision = engine.check(tool, arg_str)
    if isinstance(decision, Deny):
        return f"error: blocked by permission rule: {decision.reason}"
    if isinstance(decision, Ask):
        verdict = await _ask(approve, {
            "tool": tool,
            "command": summary,
            "reason": decision.reason,
            "suggested_pattern": suggest_pattern(tool, arg_str),
        })
        if not verdict.allowed:
            return f"error: user denied permission to {summary}"
        if verdict.remember and verdict.remember_pattern:
            rule = Rule(
                tool=tool,
                pattern=verdict.remember_pattern,
                action=verdict.remember_action,  # type: ignore[arg-type]
                reason="user-added via approval prompt",
            )
            if verdict.remember_scope == "user":
                engine.append_user_rule(rule)
            else:
                engine.append_project_rule(rule)
    return None


async def _ask(approve: ApproveFn | None, ctx: dict[str, Any]) -> Verdict:
    if approve is None:
        return Verdict(allowed=True)
    result = approve(ctx)
    if asyncio.iscoroutine(result):
        result = await result
    return result if isinstance(result, Verdict) else Verdict(allowed=bool(result))
