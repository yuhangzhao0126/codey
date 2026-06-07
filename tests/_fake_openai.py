"""Shared fake AsyncOpenAI client for context-pipeline tests."""
from __future__ import annotations

from typing import Any


class FakeStreamChunk:
    def __init__(self, content: str = "", tool_calls=None):
        self.choices = [type("C", (), {
            "delta": type("D", (), {"content": content, "tool_calls": tool_calls})()
        })()]


class FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class FakeChatCompletions:
    def __init__(self, *, response_text: str):
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return FakeAsyncStream([FakeStreamChunk(self.response_text)])
        return type("R", (), {
            "choices": [type("C", (), {
                "message": type("M", (), {"content": self.response_text})()
            })()]
        })()


class FakeClient:
    def __init__(self, *, response_text: str):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeChatCompletions(response_text=response_text)
