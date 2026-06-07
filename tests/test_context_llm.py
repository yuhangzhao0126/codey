"""Tests for llm_compact_history."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codey.config import Profile
from codey.context import llm as llm_mod
from codey.core.messages import Message


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


def _profile() -> Profile:
    return Profile(name="p", api_key="k", base_url="https://x",
                   model="m", context_window=100_000,
                   max_output_tokens=4_096, compact_headroom=13_000)


@pytest.mark.asyncio
async def test_compact_replaces_history_with_system_plus_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    history = [
        Message(role="system", content="sys prompt"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi"),
        Message(role="user", content="big task"),
    ]
    client = FakeClient(response_text="user wants X; we're doing Y")
    metas = []
    ok = await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=metas.append, client=client, recent_files=[],
    )
    assert ok is True
    assert len(history) == 2
    assert history[0].role == "system"
    assert history[0].content == "sys prompt"
    assert history[1].role == "user"
    assert "Summary of prior conversation" in history[1].content
    assert "user wants X; we're doing Y" in history[1].content
    assert "Snapshot:" in history[1].content
    assert "Conversation compacted at" in history[1].content
    assert metas and "summarized history" in metas[0]


@pytest.mark.asyncio
async def test_compact_reads_recent_files(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    f1 = tmp_path / "a.txt"; f1.write_text("alpha contents")
    f2 = tmp_path / "b.txt"; f2.write_text("beta contents")
    history = [Message(role="system", content="s"),
               Message(role="user", content="hi")]
    client = FakeClient(response_text="summary")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=[f1, f2],
    )
    body = history[1].content
    assert "alpha contents" in body
    assert "beta contents" in body
    assert f"--- {f1} ---" in body
    assert f"--- {f2} ---" in body


@pytest.mark.asyncio
async def test_compact_handles_missing_recent_file(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    missing = tmp_path / "never_existed.txt"
    history = [Message(role="user", content="hi")]
    client = FakeClient(response_text="summary")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=[missing],
    )
    assert "(error:" in history[0].content


@pytest.mark.asyncio
async def test_compact_writes_snapshot_file(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    history = [Message(role="system", content="s"),
               Message(role="user", content="u")]
    client = FakeClient(response_text="ok")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=[],
    )
    snaps = list((tmp_path / "transcripts" / "sid" / "snapshots").iterdir())
    assert len(snaps) == 1
    assert "proactive" in snaps[0].name
    data = json.loads(snaps[0].read_text())
    assert any(m.get("content") == "u" for m in data)


@pytest.mark.asyncio
async def test_compact_caps_at_max_files(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    files = []
    for i in range(8):
        p = tmp_path / f"f{i}.txt"
        p.write_text(f"contents-{i}")
        files.append(p)
    history = [Message(role="user", content="hi")]
    client = FakeClient(response_text="summary")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=files,
    )
    body = history[0].content
    for i in range(3, 8):
        assert f"contents-{i}" in body
    for i in range(0, 3):
        assert f"contents-{i}" not in body
