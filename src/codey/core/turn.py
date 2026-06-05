"""The Agent turn loop.

`Agent.run()` orchestrates one user turn:

  TurnStarted
    [ for each model round: ]
      RoundStarted(round)
      AssistantTextDelta(text)*         # streamed text
      ToolCallRequested(id, name, args) # once args fully parsed
      ToolResult(id, name, ok, content) # after local dispatch
    AssistantMessageCompleted(text)     # text concatenated across rounds
  TurnCompleted(reason, error?)

Tool-call dispatch is gated by the PreToolUse hook (permissions). PostToolUse
fires after each dispatch. Stop fires unconditionally in `finally`.

History invariants and cancellation rollback are handled here so the next
turn always starts from a clean baseline.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from ..config import Profile
from ..hooks import HookEvent, HookRegistry
from . import history as history_mod
from . import streaming as streaming_mod
from .agent import ToolRegistry
from .events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    Event,
    RoundStarted,
    ToolCallRequested,
    ToolResult,
    TurnCompleted,
    TurnStarted,
)
from .messages import Message

MAX_ROUNDS = 10  # safety cap on tool-use loops


@dataclass
class Agent:
    profile: Profile
    system_prompt: str = ""
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    hooks: HookRegistry = field(default_factory=HookRegistry)
    history: list[Message] = field(default_factory=list)
    _client: AsyncOpenAI = field(init=False)

    def __post_init__(self) -> None:
        self._client = self._build_client(self.profile)
        self.history.append(Message(role="system", content=self._compose_system_message()))

    def _compose_system_message(self) -> str:
        """User-supplied prompt + a runtime note so the model can answer
        questions like 'what model are you?' accurately."""
        runtime_note = (
            f"You are being served via the OpenAI-compatible API at "
            f"`{self.profile.base_url}` as model `{self.profile.model}` "
            f"(profile: `{self.profile.name}`). When the user asks what model "
            f"or provider you are, answer from this information."
        )
        if self.system_prompt:
            return f"{self.system_prompt}\n\n{runtime_note}"
        return runtime_note

    # -- public API --

    async def run(self, user_input: str) -> AsyncIterator[Event]:
        """Execute one user turn. Yields Events; orchestrates tool rounds.

        Fires hook events at well-defined points:
          UserPromptSubmit  before the turn starts (may rewrite or cancel)
          PreToolUse        before each tool call (may rewrite args or cancel)
          PostToolUse       after each tool call returns
          Stop              after the turn ends (in try/finally so it always runs)

        On any failure (network, API, cancellation), the partially-staged user
        message is rolled back so history stays consistent for the next turn.
        Also repairs history before the request to drop any orphaned
        `assistant.tool_calls` from a previously-interrupted turn — otherwise
        the provider will 400 with "no tool output found for function call X".
        """
        self._repair_history()

        # UserPromptSubmit — gives hooks a chance to rewrite or cancel.
        hr = await self.hooks.trigger(
            HookEvent.USER_PROMPT_SUBMIT, {"user_input": user_input}
        )
        if hr.modified_user_input is not None:
            user_input = hr.modified_user_input
        if hr.cancel:
            yield TurnStarted()
            yield TurnCompleted(reason="cancelled", error=hr.result)
            await self.hooks.trigger(HookEvent.STOP, {"reason": "cancelled", "error": hr.result})
            return

        baseline_len = len(self.history)
        self.history.append(Message(role="user", content=user_input))

        yield TurnStarted()

        assistant_text_parts: list[str] = []
        stop_reason: str = "stop"
        stop_error: str | None = None
        try:
            for round_idx in range(MAX_ROUNDS):
                yield RoundStarted(round=round_idx)

                text, tool_calls = "", []
                async for ev in self._stream_one_round():
                    if isinstance(ev, AssistantTextDelta):
                        text += ev.text
                        yield ev
                    elif isinstance(ev, streaming_mod.RoundDone):
                        tool_calls = ev.tool_calls
                        break

                # Persist this round's assistant message exactly as the API saw it.
                assistant_text_parts.append(text)
                self.history.append(
                    Message(role="assistant", content=text, tool_calls=tool_calls or None)
                )

                if not tool_calls:
                    break  # natural stop

                # Dispatch all tool calls concurrently. Per-call hook order
                # (PRE → request event → dispatch → result event → POST) is
                # preserved inside _dispatch_one. Inter-call order is not.
                #
                # OpenAI matches tool responses by tool_call_id, not position,
                # so nondeterministic history-append order is wire-safe.
                async def _dispatch_one(call: dict) -> list[Event]:
                    call_id = call["id"]
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    out: list[Event] = []

                    pre = await self.hooks.trigger(HookEvent.PRE_TOOL_USE, {
                        "tool": name, "arguments": args, "call_id": call_id,
                    })
                    if pre.modified_arguments is not None:
                        args = pre.modified_arguments

                    out.append(ToolCallRequested(id=call_id, name=name, arguments=args))

                    if pre.cancel:
                        ok = False
                        content = pre.result or "error: tool cancelled by hook"
                    else:
                        ok, content = await self.tools.dispatch(name, args)

                    # Capture index BEFORE append so a post-hook rewrite lands
                    # on this call's message even with concurrent appends from
                    # other _dispatch_one tasks.
                    idx = len(self.history)
                    self.history.append(
                        Message(role="tool", tool_call_id=call_id, name=name, content=content)
                    )
                    out.append(ToolResult(id=call_id, name=name, ok=ok, content=content))

                    post_payload = {
                        "tool": name, "arguments": args, "call_id": call_id,
                        "ok": ok, "result": content,
                    }
                    post = await self.hooks.trigger(HookEvent.POST_TOOL_USE, post_payload)
                    if post.modified_post_result is not None:
                        new_content = post_payload["result"]
                        self.history[idx].content = new_content
                        # Patch the ToolResult event we appended above so the
                        # event stream and history agree on what the model saw.
                        out[-1] = ToolResult(id=call_id, name=name, ok=ok, content=new_content)

                    return out

                per_call_events = await asyncio.gather(
                    *[_dispatch_one(call) for call in tool_calls]
                )
                for events in per_call_events:
                    for ev in events:
                        yield ev
            else:
                stop_reason = "error"
                stop_error = f"hit MAX_ROUNDS ({MAX_ROUNDS})"
                yield TurnCompleted(reason="error", error=stop_error)
                return

            yield AssistantMessageCompleted(text="".join(assistant_text_parts))
            yield TurnCompleted(reason="stop")

        except (KeyboardInterrupt, GeneratorExit):
            del self.history[baseline_len:]
            stop_reason = "cancelled"
            yield TurnCompleted(reason="cancelled")
            raise
        except BaseException as e:  # noqa: BLE001
            # Broad catch: provider error, malformed SSE (JSONDecodeError),
            # timeout, anything mid-stream. We roll back what we appended this
            # turn so the next request starts clean, then surface the error.
            del self.history[baseline_len:]
            stop_reason = "error"
            stop_error = f"{type(e).__name__}: {e}"
            yield TurnCompleted(reason="error", error=stop_error)
        finally:
            # Stop fires unconditionally so cleanup hooks (audit log flushes,
            # token counters, summaries) always run.
            await self.hooks.trigger(
                HookEvent.STOP, {"reason": stop_reason, "error": stop_error}
            )

    def _repair_history(self) -> None:
        history_mod.repair(self.history)

    def reset(self) -> None:
        """Clear chat history, keeping the system message. Also clears any
        per-session tool state that survives outside of history — currently
        just the todo_write task list."""
        self.history = history_mod.reset_non_system(self.history)
        todo_tool = self.tools.tools.get("todo_write")
        if todo_tool is not None and hasattr(todo_tool, "todos"):
            todo_tool.todos = []

    async def swap_profile(self, profile: Profile) -> None:
        """Switch provider/model live. Keeps chat history; closes the old client.
        Rebuilds the system message so the in-prompt runtime note (base_url +
        model + profile name) reflects the new profile.

        Caveat: existing history may include provider-specific fields (tool_calls,
        etc.) that the new model handles differently. If the next `run()` fails,
        call `reset()` and try again.
        """
        old = self._client
        self.profile = profile
        self._client = self._build_client(profile)

        # Refresh the in-history system message so the model knows where it's running.
        new_system = self._compose_system_message()
        non_system = [m for m in self.history if m.role != "system"]
        self.history = [Message(role="system", content=new_system)] + non_system

        try:
            await old.close()
        except Exception:  # noqa: BLE001
            pass

    async def aclose(self) -> None:
        """Release the underlying HTTP client. Safe to call multiple times."""
        try:
            await self._client.close()
        except Exception:  # noqa: BLE001
            pass

    # -- internals --

    @staticmethod
    def _build_client(profile: Profile) -> AsyncOpenAI:
        return AsyncOpenAI(api_key=profile.api_key, base_url=profile.base_url)

    async def _stream_one_round(self) -> AsyncIterator[AssistantTextDelta | "streaming_mod.RoundDone"]:
        """Thin wrapper around core.streaming.stream_one_round so tests can
        monkeypatch the per-instance method. Yields AssistantTextDelta and
        exactly one RoundDone sentinel."""
        async for ev in streaming_mod.stream_one_round(
            self._client, self.profile, self.history, self.tools.schemas()
        ):
            yield ev


# Back-compat alias for tests / external callers that import _RoundDone from
# codey.core.turn. The canonical name is core.streaming.RoundDone.
_RoundDone = streaming_mod.RoundDone
