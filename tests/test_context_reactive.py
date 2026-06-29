"""Tests for reactive_compact — the post-413 recovery path."""
from __future__ import annotations

from typing import Any

import pytest

from codey.config import Provider
from codey.context import reactive as reactive_mod
from codey.core.messages import Message


class _FakeChatCompletions:
    def __init__(self, *, response_text: str):
        self.response_text = response_text

    async def create(self, **kwargs):
        return type("R", (), {
            "choices": [type("C", (), {
                "message": type("M", (), {"content": self.response_text})()
            })()]
        })()


class FakeClient:
    def __init__(self, *, response_text: str):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeChatCompletions(response_text=response_text)


def _provider():
    return Provider(name="p", api_key="k", base_url="x", model="m",
                   context_window=100_000, max_output_tokens=4_096,
                   compact_headroom=13_000)


def _hist(n_body: int):
    h = [Message(role="system", content="sys")]
    for i in range(n_body):
        h.append(Message(role="user", content=f"u{i}"))
    return h


@pytest.mark.asyncio
async def test_reactive_keeps_system_summary_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist(20)
    client = FakeClient(response_text="summary text")
    metas = []
    await reactive_mod.run(
        history=hist, provider=_provider(), session_id="sid",
        meta=metas.append, client=client, recent_files=[],
    )
    assert hist[0].role == "system"
    assert hist[1].role == "user"
    assert "Reactive compact triggered" in hist[1].content
    assert "summary text" in hist[1].content
    tail = hist[2:]
    assert len(tail) == reactive_mod.REACTIVE_TAIL
    assert tail[-1].content == "u19"
    assert any("reactive compact triggered" in m for m in metas)


@pytest.mark.asyncio
async def test_reactive_expands_tail_to_pair_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    body = [Message(role="user", content=f"u{i}") for i in range(10)]
    body.append(Message(role="assistant", content="", tool_calls=[
        {"id": "c1", "type": "function",
         "function": {"name": "t", "arguments": "{}"}},
    ]))
    body.append(Message(role="tool", tool_call_id="c1", name="t", content="r"))
    body.extend(Message(role="user", content=f"v{i}") for i in range(3))
    hist = [Message(role="system", content="s")] + body
    await reactive_mod.run(
        history=hist, provider=_provider(), session_id="sid",
        meta=lambda _m: None, client=FakeClient(response_text="ok"),
        recent_files=[],
    )
    tail = hist[2:]
    if any(m.role == "tool" for m in tail):
        for i, m in enumerate(tail):
            if m.role == "tool":
                assert i > 0
                assert tail[i - 1].role == "assistant"
                assert tail[i - 1].tool_calls
                break


@pytest.mark.asyncio
async def test_reactive_writes_reactive_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist(10)
    await reactive_mod.run(
        history=hist, provider=_provider(), session_id="sid",
        meta=lambda _m: None, client=FakeClient(response_text="s"),
        recent_files=[],
    )
    snaps = list((tmp_path / "transcripts" / "sid" / "snapshots").iterdir())
    assert len(snaps) == 1
    assert "reactive" in snaps[0].name
