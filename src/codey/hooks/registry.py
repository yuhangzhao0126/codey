"""Hook system.

A simple in-process pub/sub for the agent loop. Hooks let user code observe,
decide, or modify the flow at well-defined points without touching agent
internals.

Events
------
- UserPromptSubmit: after the user types, before the turn starts
- PreToolUse:       after the model emits a tool_call, before dispatch
- PostToolUse:      after dispatch returns
- Stop:             after the turn ends (any reason)
- SessionStart:     once per session, after agent construction

Each event runs every registered hook in registration order. Results are
merged: any `cancel=True` wins; modifications stack so a later hook sees the
output of earlier ones.

Hook callbacks may be sync or async; sync callbacks are wrapped. Exceptions
in one hook are caught and reported (best-effort) without breaking the chain.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable


class HookEvent(str, Enum):
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"
    SESSION_START = "SessionStart"


@dataclass
class HookResult:
    """Return value from a hook callback.

    A hook that just observes can return `HookResult()` (or None — None is
    coerced to an empty result).

    Fields:
      cancel:                PreToolUse only: skip dispatch; UserPromptSubmit:
                             skip the whole turn.
      result:                What to feed back to the model as the tool's
                             content when cancel=True (PreToolUse). Ignored
                             elsewhere.
      modified_arguments:    PreToolUse only. Rewrites the args passed to the
                             tool. Subsequent hooks see this rewrite.
      modified_user_input:   UserPromptSubmit only. Rewrites the user input
                             before it goes into history.
      modified_post_result:  PostToolUse only. Rewrites the tool result text
                             both subsequent hooks see (via payload) and the
                             agent records in history.
    """
    cancel: bool = False
    result: str | None = None
    modified_arguments: dict[str, Any] | None = None
    modified_user_input: str | None = None
    modified_post_result: str | None = None


# A hook callback receives the payload dict and returns a HookResult or None.
HookCallback = Callable[[dict[str, Any]], "HookResult | None | Awaitable[HookResult | None]"]


@dataclass
class Hook:
    name: str
    event: HookEvent
    callback: HookCallback
    enabled: bool = True


@dataclass
class HookRegistry:
    """Holds hooks per event. Stateless beyond that; safe to share across
    Agents within one session."""

    _by_event: dict[HookEvent, list[Hook]] = field(default_factory=dict)
    # Optional sink for error notes ("hook X raised Y"). The host (CLI/TUI)
    # supplies a function so messages land in the right place.
    error_sink: Callable[[str], None] | None = None

    # -- registration --

    def register(self, event: HookEvent, callback: HookCallback, name: str) -> Hook:
        """Add a hook. Duplicate names within the same event raise ValueError."""
        bucket = self._by_event.setdefault(event, [])
        if any(h.name == name for h in bucket):
            raise ValueError(f"hook {name!r} already registered for {event.value}")
        hook = Hook(name=name, event=event, callback=callback)
        bucket.append(hook)
        return hook

    def unregister(self, name: str, event: HookEvent | None = None) -> bool:
        """Remove a hook by name. If event is None, search all events."""
        events = [event] if event else list(self._by_event)
        for ev in events:
            bucket = self._by_event.get(ev, [])
            for i, h in enumerate(bucket):
                if h.name == name:
                    del bucket[i]
                    return True
        return False

    def enable(self, name: str) -> bool:
        return self._set_enabled(name, True)

    def disable(self, name: str) -> bool:
        return self._set_enabled(name, False)

    def _set_enabled(self, name: str, enabled: bool) -> bool:
        for bucket in self._by_event.values():
            for h in bucket:
                if h.name == name:
                    h.enabled = enabled
                    return True
        return False

    def list(self, event: HookEvent | None = None) -> list[Hook]:
        if event is not None:
            return list(self._by_event.get(event, []))
        return [h for bucket in self._by_event.values() for h in bucket]

    # -- dispatch --

    async def trigger(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        """Run all enabled hooks for `event` in registration order.

        Modifications stack: later hooks see earlier modifications.
        Any `cancel=True` wins. `result` from the *last* cancelling hook is
        what propagates.
        """
        merged = HookResult()
        # We mutate `payload` in place when a hook returns modified_arguments
        # or modified_user_input, so later hooks see the updated values.
        for hook in self._by_event.get(event, []):
            if not hook.enabled:
                continue
            try:
                hr = await self._invoke(hook.callback, payload)
            except Exception as e:  # noqa: BLE001
                self._note_error(hook, e)
                continue
            if hr is None:
                continue
            if hr.cancel:
                merged.cancel = True
                # Last cancelling hook wins for the user-visible result.
                if hr.result is not None:
                    merged.result = hr.result
            if hr.modified_arguments is not None:
                merged.modified_arguments = hr.modified_arguments
                payload["arguments"] = hr.modified_arguments
            if hr.modified_user_input is not None:
                merged.modified_user_input = hr.modified_user_input
                payload["user_input"] = hr.modified_user_input
            if hr.modified_post_result is not None:
                merged.modified_post_result = hr.modified_post_result
                payload["result"] = hr.modified_post_result
        return merged

    @staticmethod
    async def _invoke(cb: HookCallback, payload: dict[str, Any]) -> HookResult | None:
        result = cb(payload)
        if inspect.isawaitable(result):
            result = await result
        if result is None or isinstance(result, HookResult):
            return result
        raise TypeError(
            f"hook returned {type(result).__name__}; expected HookResult or None"
        )

    def _note_error(self, hook: Hook, exc: BaseException) -> None:
        msg = f"[hook error] {hook.name} ({hook.event.value}): {type(exc).__name__}: {exc}"
        if self.error_sink is not None:
            try:
                self.error_sink(msg)
            except Exception:  # noqa: BLE001
                pass
