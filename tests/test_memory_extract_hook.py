"""Tests for the Stop-hook memory extract callback."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from codey.core.messages import Message
from codey.hooks.builtin.memory_extract import build_memory_extract_hook
from codey.memory.registry import MemoryRegistry
from codey.memory.store import MemoryStore


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class FakeCompletions:
    def __init__(self, payloads: list[str]) -> None:
        self._payloads = list(payloads)

    async def create(self, **_kw) -> _FakeResp:
        return _FakeResp(self._payloads.pop(0) if self._payloads else "[]")


class FakeChat:
    def __init__(self, payloads: list[str]) -> None:
        self.completions = FakeCompletions(payloads)


class FakeClient:
    def __init__(self, payloads: list[str]) -> None:
        self.chat = FakeChat(payloads)


@pytest.mark.asyncio
async def test_memory_extract_writes_novel_entry(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "a.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "a.jsonl")
    history = [
        Message(role="system", content="sys"),
        Message(role="user", content="always use pnpm in this repo, never npm"),
        Message(role="assistant", content="got it"),
    ]
    client = FakeClient([
        '[{"name":"use_pnpm","description":"prefer pnpm","body":"Always pnpm.","type":"preference","scope":"project"}]',
        '{"verdict":"NOVEL"}',
    ])
    cb = build_memory_extract_hook(
        history_provider=lambda: history,
        registry=reg, store=store,
        client_provider=lambda: client, model="m",
        session_id="sid", queue_path=tmp_path / "queue.jsonl",
    )
    await cb({"reason": "stop"})
    task = cb._last_task  # type: ignore[attr-defined]
    assert task is not None
    await task
    assert (p / "use_pnpm.md").exists()


@pytest.mark.asyncio
async def test_memory_extract_no_op_on_empty_history(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "a.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "a.jsonl")
    cb = build_memory_extract_hook(
        history_provider=lambda: [Message(role="system", content="x")],
        registry=reg, store=store,
        client_provider=lambda: FakeClient([]),
        model="m", session_id="sid",
        queue_path=tmp_path / "queue.jsonl",
    )
    await cb({"reason": "stop"})
    assert cb._last_task is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_memory_extract_queue_acked_on_success(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "a.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "a.jsonl")
    qpath = tmp_path / "queue.jsonl"
    cb = build_memory_extract_hook(
        history_provider=lambda: [
            Message(role="user", content="trivial"),
        ],
        registry=reg, store=store,
        client_provider=lambda: FakeClient(["[]"]),
        model="m", session_id="sid", queue_path=qpath,
    )
    await cb({"reason": "stop"})
    if cb._last_task is not None:  # type: ignore[attr-defined]
        await cb._last_task  # type: ignore[attr-defined]
    if qpath.exists():
        text = qpath.read_text().strip()
        assert text == ""
