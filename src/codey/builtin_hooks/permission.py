"""PreToolUse hook: enforces PermissionEngine decisions.

Replaces the per-tool _gate.py call. Every tool that goes through Agent.run()
is gated here uniformly. New tools added later are protected automatically.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from ..hooks import HookCallback, HookResult
from ..permissions import Ask, Deny, PermissionEngine, Rule, Verdict, suggest_pattern

# An approve callback is supplied by the host (CLI/TUI). It receives a context
# dict (tool, command, reason, suggested_pattern) and returns a Verdict — see
# tools.bash.Verdict. We import lazily inside the hook to avoid a circular dep
# at module-load time.

ApproveFn = Callable[[dict[str, Any]], "Any"]


# How to extract the "canonical arg string" used for rule matching.
# Keeps the lookup in one place; new tools opt in by adding an entry here.
CANONICAL_ARG: dict[str, Callable[[dict[str, Any]], str]] = {
    "bash":       lambda a: (a.get("command") or "").strip(),
    "read_file":  lambda a: (a.get("path") or "").strip(),
    "list_dir":   lambda a: (a.get("path") or ".").strip() or ".",
    "grep":       lambda a: (a.get("path") or ".").strip() or ".",
    "write_file": lambda a: (a.get("path") or "").strip(),
    "apply_edit": lambda a: (a.get("path") or "").strip(),
}


def _summary(tool: str, args: dict[str, Any]) -> str:
    """Human-readable line shown in the approval modal."""
    if tool == "bash":
        return args.get("command", "")
    if tool == "write_file":
        content = args.get("content") or ""
        return f"write {len(content)} chars to {args.get('path', '')}"
    if tool == "apply_edit":
        return f"edit {args.get('path', '')}"
    if tool in ("read_file", "list_dir"):
        return f"read {args.get('path', '')}"
    if tool == "grep":
        pat = args.get("pattern", "")
        path = args.get("path", ".")
        return f"grep {pat!r} under {path}"
    return f"{tool}({args})"


def permission_check_hook(
    engine: PermissionEngine,
    approve: ApproveFn | None,
) -> HookCallback:
    """Return a PreToolUse callback that consults the engine and possibly prompts.

    On Deny: HookResult(cancel=True, result="error: blocked …").
    On Ask: invoke approve(); if denied, cancel with "error: user denied …";
            if remember, append a rule to the engine's user/project store.
    On Allow: HookResult() (let the dispatch proceed).
    """
    async def hook(payload: dict[str, Any]) -> HookResult | None:
        tool = payload["tool"]
        args = payload["arguments"]
        canonical = CANONICAL_ARG.get(tool, lambda a: "")(args)
        decision = engine.check(tool, canonical)
        if isinstance(decision, Deny):
            return HookResult(
                cancel=True,
                result=f"error: blocked by permission rule: {decision.reason}",
            )
        if isinstance(decision, Ask):
            verdict = await _ask(approve, {
                "tool": tool,
                "command": _summary(tool, args),
                "reason": decision.reason,
                "suggested_pattern": suggest_pattern(tool, canonical),
            })
            if not verdict.allowed:
                return HookResult(
                    cancel=True,
                    result=f"error: user denied permission to {_summary(tool, args)}",
                )
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
        return None  # Allow / proceed
    return hook


async def _ask(approve: ApproveFn | None, ctx: dict[str, Any]):
    """Wrap the approve callback. Returns a Verdict (always)."""
    if approve is None:
        return Verdict(allowed=True)
    result = approve(ctx)
    if asyncio.iscoroutine(result):
        result = await result
    return result if isinstance(result, Verdict) else Verdict(allowed=bool(result))
