"""Stream one model response and reassemble fragmented tool_calls.

Pulled out of agent.py so the Agent's turn loop is shorter and the streaming
logic (which reassembles tool_calls that arrive in pieces keyed by `index`)
is independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from ..config import Provider
from ..context.errors import PromptTooLongError, sniff as _sniff_provider_error
from .events import AssistantTextDelta
from .messages import Message


@dataclass
class RoundDone:
    """End-of-round sentinel carrying any fully-assembled tool calls."""
    tool_calls: list[dict[str, Any]]


async def stream_one_round(
    client: AsyncOpenAI,
    provider: Provider,
    history: list[Message],
    tool_schemas: list[dict[str, Any]],
) -> AsyncIterator[AssistantTextDelta | RoundDone]:
    """Stream a single model response.

    Yields AssistantTextDelta for streamed text, then exactly one RoundDone
    carrying the (possibly empty) list of tool calls assembled from the
    fragments the API streamed.
    """
    kwargs: dict[str, Any] = {
        "model": provider.model,
        "messages": [m.to_wire() for m in history],
        "stream": True,
    }
    if tool_schemas:
        kwargs["tools"] = tool_schemas

    try:
        stream = await client.chat.completions.create(**kwargs)
    except BaseException as exc:
        sniffed = _sniff_provider_error(exc)
        if sniffed is not None:
            raise sniffed from exc
        raise

    # tool_calls stream in fragments keyed by `index`; reassemble here.
    partial: dict[int, dict[str, Any]] = {}

    try:
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
    except PromptTooLongError:
        raise
    except BaseException as exc:
        sniffed = _sniff_provider_error(exc)
        if sniffed is not None:
            raise sniffed from exc
        raise

    tool_calls = [partial[i] for i in sorted(partial)] if partial else []
    yield RoundDone(tool_calls=tool_calls)
