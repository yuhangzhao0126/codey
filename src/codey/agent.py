"""Core agent loop.

The agent orchestrates one *turn* per `run()` call. A turn may involve multiple
rounds with the model — for tool use, the model can emit `tool_calls`, the agent
executes them locally, feeds results back, and loops until the model returns
a stop. (Tool execution machinery is stubbed for now; see ToolRegistry below.)

`run()` is an async generator that yields tagged `Event` objects describing what
is happening, so UIs (CLI today, Textual later) only consume and render — they
do not orchestrate. This keeps the agent loop fully owned by this module.

Event flow for a single user turn:

    TurnStarted
      [ for each model round: ]
        RoundStarted(round)
        AssistantTextDelta(text)*         # streamed text
        ToolCallRequested(id, name, args) # once args fully parsed
        ToolResult(id, name, ok, content) # after local dispatch
      AssistantMessageCompleted(text)     # text concatenated across rounds
    TurnCompleted(reason, error?)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Protocol

from openai import AsyncOpenAI, OpenAIError

from .config import Profile
from .hooks import HookEvent, HookRegistry


# ---------- message schema ----------

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str = ""
    # Assistant turns that requested tool use carry tool_calls.
    tool_calls: list[dict[str, Any]] | None = None
    # Tool result turns reference the originating call.
    tool_call_id: str | None = None
    name: str | None = None  # for tool messages: tool name

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the OpenAI chat-completions wire format."""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg


# ---------- events emitted by run() ----------

@dataclass
class TurnStarted:
    pass


@dataclass
class RoundStarted:
    round: int  # 0-indexed


@dataclass
class AssistantTextDelta:
    text: str


@dataclass
class AssistantMessageCompleted:
    text: str  # full assistant text for the turn (concatenated across rounds)


@dataclass
class ToolCallRequested:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    id: str
    name: str
    ok: bool
    content: str


@dataclass
class TurnCompleted:
    reason: Literal["stop", "error", "cancelled"]
    error: str | None = None


Event = (
    TurnStarted
    | RoundStarted
    | AssistantTextDelta
    | AssistantMessageCompleted
    | ToolCallRequested
    | ToolResult
    | TurnCompleted
)


# ---------- tool registry (stubbed; real tools land in a later commit) ----------

class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema

    async def run(self, arguments: dict[str, Any]) -> str: ...


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI tool-spec list. Empty list disables tool use."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.tools.values()
        ]

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        tool = self.tools.get(name)
        if tool is None:
            return False, f"unknown tool: {name}"
        try:
            return True, await tool.run(arguments)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"


# ---------- the agent itself ----------

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
                    elif isinstance(ev, _RoundDone):
                        tool_calls = ev.tool_calls
                        break
                    yield ev

                # Persist this round's assistant message exactly as the API saw it.
                assistant_text_parts.append(text)
                self.history.append(
                    Message(role="assistant", content=text, tool_calls=tool_calls or None)
                )

                if not tool_calls:
                    break  # natural stop

                # Dispatch each tool call and append results to history.
                for call in tool_calls:
                    call_id = call["id"]
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    # PreToolUse — hooks may rewrite args or cancel.
                    pre = await self.hooks.trigger(HookEvent.PRE_TOOL_USE, {
                        "tool": name, "arguments": args, "call_id": call_id,
                    })
                    if pre.modified_arguments is not None:
                        args = pre.modified_arguments

                    yield ToolCallRequested(id=call_id, name=name, arguments=args)

                    if pre.cancel:
                        ok = False
                        content = pre.result or "error: tool cancelled by hook"
                    else:
                        ok, content = await self.tools.dispatch(name, args)
                    self.history.append(
                        Message(role="tool", tool_call_id=call_id, name=name, content=content)
                    )
                    yield ToolResult(id=call_id, name=name, ok=ok, content=content)

                    # PostToolUse — observers, plus may rewrite the result the
                    # model sees via HookResult.modified_post_result. We re-read
                    # `payload["result"]` after triggering because the hook
                    # registry mutates it in place for chained hooks.
                    post_payload = {
                        "tool": name, "arguments": args, "call_id": call_id,
                        "ok": ok, "result": content,
                    }
                    post = await self.hooks.trigger(HookEvent.POST_TOOL_USE, post_payload)
                    if post.modified_post_result is not None:
                        new_content = post_payload["result"]
                        # Patch the tool Message we just appended a few lines up
                        # so history reflects what the model will read.
                        self.history[-1].content = new_content
                        content = new_content
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
        """Drop trailing assistant.tool_calls messages whose tool results never
        landed (e.g. user cancelled mid-tool-call last turn, or a tool dispatch
        raised). Walks from the end backwards: any assistant message with
        tool_calls must be followed by one `role:"tool"` message per call_id."""
        while self.history:
            last = self.history[-1]
            if last.role == "assistant" and last.tool_calls:
                self.history.pop()
                continue
            if last.role == "tool":
                # tool result with no preceding assistant tool_calls is also junk.
                # Check by walking back to find the matching assistant call.
                ids_seen: set[str] = set()
                i = len(self.history) - 1
                while i >= 0 and self.history[i].role == "tool":
                    if self.history[i].tool_call_id:
                        ids_seen.add(self.history[i].tool_call_id)
                    i -= 1
                if i < 0 or self.history[i].role != "assistant" or not self.history[i].tool_calls:
                    # orphaned tool block; drop just the trailing tool messages
                    del self.history[i + 1:]
                    continue
                expected = {c["id"] for c in self.history[i].tool_calls if c.get("id")}
                if expected - ids_seen:
                    # Some tool results are missing for this assistant call;
                    # drop the whole assistant + tool block.
                    del self.history[i:]
                    continue
            break

    def reset(self) -> None:
        """Clear chat history, keeping the system message."""
        self.history = [m for m in self.history if m.role == "system"]

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

    async def _stream_one_round(self) -> AsyncIterator[Event | "_RoundDone"]:
        """Stream a single model response. Yields text deltas, then a _RoundDone
        sentinel carrying any fully-assembled tool calls."""
        kwargs: dict[str, Any] = {
            "model": self.profile.model,
            "messages": [m.to_wire() for m in self.history],
            "stream": True,
        }
        schemas = self.tools.schemas()
        if schemas:
            kwargs["tools"] = schemas

        stream = await self._client.chat.completions.create(**kwargs)

        # tool_calls stream in fragments keyed by `index`; reassemble here.
        partial: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                yield AssistantTextDelta(text=delta.content)

            for tc in delta.tool_calls or []:
                slot = partial.setdefault(
                    tc.index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["function"]["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["function"]["arguments"] += tc.function.arguments

        tool_calls = [partial[i] for i in sorted(partial)] if partial else []
        yield _RoundDone(tool_calls=tool_calls)


@dataclass
class _RoundDone:
    """Internal sentinel: end of one model round, with any tool calls collected."""
    tool_calls: list[dict[str, Any]]
