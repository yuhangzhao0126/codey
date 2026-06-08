"""Verify codey.core.streaming maps provider context-overflow errors to
PromptTooLongError."""
from __future__ import annotations

import pytest

from codey.config import Profile
from codey.context.errors import PromptTooLongError
from codey.core import streaming as streaming_mod


class FakeOpenAIError(Exception):
    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class FakeChatCompletions:
    def __init__(self, exc):
        self.exc = exc

    async def create(self, **kwargs):
        raise self.exc


class FakeClient:
    def __init__(self, exc):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeChatCompletions(exc)


def _profile():
    return Profile(name="p", api_key="k", base_url="x", model="m")


@pytest.mark.asyncio
async def test_stream_one_round_wraps_context_length_exceeded():
    client = FakeClient(FakeOpenAIError(
        "This model's maximum context length is X tokens",
        code="context_length_exceeded",
    ))
    with pytest.raises(PromptTooLongError):
        async for _ in streaming_mod.stream_one_round(client, _profile(), [], []):
            pass


@pytest.mark.asyncio
async def test_stream_one_round_wraps_http_413():
    client = FakeClient(FakeOpenAIError("too big", status_code=413))
    with pytest.raises(PromptTooLongError):
        async for _ in streaming_mod.stream_one_round(client, _profile(), [], []):
            pass


@pytest.mark.asyncio
async def test_stream_one_round_lets_unrelated_errors_through():
    class Boom(Exception):
        pass
    client = FakeClient(Boom("rate limit"))
    with pytest.raises(Boom):
        async for _ in streaming_mod.stream_one_round(client, _profile(), [], []):
            pass
