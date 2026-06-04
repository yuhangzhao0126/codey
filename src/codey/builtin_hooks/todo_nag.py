"""todo_nag: gently remind the model to use todo_write for multi-step work.

Three closure-sharing hooks built from one factory:
  pre   — PreToolUse: counts rounds since the last todo_write call.
  post  — PostToolUse: once the counter passes the threshold, appends a
          reminder to the tool result the model sees on the next round.
          Fires at most once per turn.
  stop  — Stop: resets the per-turn "already nagged" flag.

The counter itself is per-session (across turns); only the "already
nagged" latch resets per turn. Rationale: if the model went 10 rounds
without planning, we want one reminder per turn — not a fresh count
just because turns ended.
"""

from __future__ import annotations

from typing import Any

from ..hooks import HookCallback, HookResult

DEFAULT_THRESHOLD = 3

REMINDER_TEMPLATE = (
    "\n\n[reminder: this turn has run {rounds} tool rounds without calling "
    "todo_write. For complex or multi-step work, call todo_write first to "
    "lay out the plan, then update item statuses as you complete them.]"
)


def build_todo_nag_hooks(threshold: int = DEFAULT_THRESHOLD) -> tuple[
    HookCallback, HookCallback, HookCallback
]:
    """Return (pre_tool_use, post_tool_use, stop) callbacks sharing state."""
    state = {"rounds_since_todo": 0, "nagged_this_turn": False}

    def pre(payload: dict[str, Any]) -> HookResult | None:
        if payload.get("tool") == "todo_write":
            state["rounds_since_todo"] = 0
            return None
        state["rounds_since_todo"] += 1
        return None

    def post(payload: dict[str, Any]) -> HookResult | None:
        if payload.get("tool") == "todo_write":
            return None
        if state["nagged_this_turn"]:
            return None
        if state["rounds_since_todo"] <= threshold:
            return None
        state["nagged_this_turn"] = True
        original = payload.get("result") or ""
        reminder = REMINDER_TEMPLATE.format(rounds=state["rounds_since_todo"])
        return HookResult(modified_post_result=original + reminder)

    def stop(payload: dict[str, Any]) -> HookResult | None:
        state["nagged_this_turn"] = False
        return None

    return pre, post, stop
