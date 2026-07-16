"""Tests for session_store integration into Agent + Session.build_resumed."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import codey.session_store.store as ss_mod
from codey.config import Provider
from codey.core.messages import Message
from codey.core.turn import Agent
from codey.session_store import SessionResumeError, SessionStore


@pytest.mark.asyncio
async def test_agent_appends_each_message_to_store(tmp_path: Path) -> None:
    prof = Provider(name="t", base_url="http://x", api_key="k", model="m",
                   context_window=200_000, max_output_tokens=8000,
                   compact_headroom=13000)
    store = SessionStore(session_id="sid-xyz", root=tmp_path)
    store.save_meta(workspace=str(tmp_path), provider="t", started_at="t0")

    agent = Agent(provider=prof, session_id="sid-xyz", _session_store=store)
    # __post_init__ already appended the system message
    agent._append_history(Message(role="user", content="hello"))
    agent._append_history(Message(role="assistant", content="hi"))
    await agent.aclose()

    loaded = store.load_history()
    assert [m.role for m in loaded] == ["system", "user", "assistant"]
    assert loaded[1].content == "hello"


@pytest.mark.asyncio
async def test_run_updates_meta_message_count(monkeypatch, tmp_path) -> None:
    """A completed turn refreshes message_count in meta.json so the resume
    picker shows a real count instead of the initial 0."""
    from codey.core.streaming import RoundDone
    from codey.core.events import AssistantTextDelta

    prof = Provider(name="t", base_url="http://x", api_key="k", model="m",
                   context_window=200_000, max_output_tokens=8000,
                   compact_headroom=13000)
    store = SessionStore(session_id="cnt12345", root=tmp_path)
    store.save_meta(workspace=str(tmp_path), provider="t", started_at="t0")
    assert store.load_meta().message_count == 0  # initial

    agent = Agent(provider=prof, session_id="cnt12345", _session_store=store)

    async def fake_stream(self):
        yield AssistantTextDelta(text="hi")
        yield RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    async for _ in agent.run("hello"):
        pass
    await agent.aclose()

    # system + user + assistant = 3
    assert store.load_meta().message_count == 3
    assert store.load_meta().last_at != "t0"  # refreshed


@pytest.mark.asyncio
async def test_resume_replay_drops_orphan_tool_calls(tmp_path: Path) -> None:
    """An orphan assistant tool_call from a crashed turn is dropped by repair."""
    from codey.core import history as history_mod

    store = SessionStore(session_id="orphan", root=tmp_path)
    store.save_meta(workspace=str(tmp_path), provider="t", started_at="t0")
    store.append_message(Message(role="system", content="sys"))
    store.append_message(Message(role="user", content="run a tool"))
    store.append_message(Message(
        role="assistant", content="",
        tool_calls=[{"id": "c1", "type": "function",
                     "function": {"name": "bash", "arguments": "{}"}}],
    ))
    # No tool response written — simulate crash.

    history = store.load_history()
    history_mod.repair(history)
    for m in history:
        if m.role == "assistant" and m.tool_calls:
            # the orphan would still carry tool_calls if NOT dropped; check repair handled it
            for c in m.tool_calls:
                assert c["id"] != "c1" or False, "orphan tool_call survived"


@pytest.mark.asyncio
async def test_session_build_resumed_restores_history(
    tmp_path: Path, temp_config, monkeypatch
) -> None:
    """Session.build_resumed loads a prior session's history + provider."""
    from codey.core.session import Session

    monkeypatch.setattr(ss_mod, "_DEFAULT_ROOT", tmp_path / "transcripts")

    ws = tmp_path / "ws"
    ws.mkdir()

    store = SessionStore(session_id="prev1234", root=tmp_path / "transcripts")
    store.save_meta(workspace=str(ws), provider="alpha", started_at="2026-06-13T10:00:00")
    store.append_message(Message(role="system", content="ignored — Session re-composes"))
    store.append_message(Message(role="user", content="prior turn"))
    store.append_message(Message(role="assistant", content="prior answer"))

    sinks = SimpleNamespace(
        transcript_writer=None,
        meta_writer=lambda *_: None,
        approve=lambda _ctx: None,
        todo_writer=None,
    )
    sess = Session.build_resumed(
        session_id="prev1234",
        provider_arg=None,
        ui_sinks=sinks,
        workspace=ws,
    )

    assert sess.session_id == "prev1234"
    assert sess.workspace == ws.resolve()
    roles = [m.role for m in sess.agent.history]
    assert roles.count("user") >= 1
    assert roles.count("assistant") >= 1
    await sess.aclose()


@pytest.mark.asyncio
async def test_session_build_resumed_raises_when_workspace_gone(
    tmp_path: Path, temp_config, monkeypatch
) -> None:
    from codey.core.session import Session

    monkeypatch.setattr(ss_mod, "_DEFAULT_ROOT", tmp_path / "transcripts")
    gone = tmp_path / "vanished"  # does not exist

    store = SessionStore(session_id="gone1234", root=tmp_path / "transcripts")
    store.save_meta(workspace=str(gone), provider="alpha", started_at="t")

    sinks = SimpleNamespace(
        transcript_writer=None,
        meta_writer=lambda *_: None,
        approve=lambda _ctx: None,
        todo_writer=None,
    )
    with pytest.raises(SessionResumeError):
        Session.build_resumed(session_id="gone1234", provider_arg=None,
                              ui_sinks=sinks)


@pytest.mark.asyncio
async def test_session_build_resumed_raises_when_provider_gone(
    tmp_path: Path, temp_config, monkeypatch
) -> None:
    """A session whose provider is no longer configured raises
    SessionResumeError (not a bare RuntimeError)."""
    from codey.core.session import Session

    monkeypatch.setattr(ss_mod, "_DEFAULT_ROOT", tmp_path / "transcripts")
    ws = tmp_path / "ws"
    ws.mkdir()

    store = SessionStore(session_id="ghost123", root=tmp_path / "transcripts")
    # "ghost" is not in temp_config (which has alpha + beta)
    store.save_meta(workspace=str(ws), provider="ghost", started_at="t")

    sinks = SimpleNamespace(
        transcript_writer=None,
        meta_writer=lambda *_: None,
        approve=lambda _ctx: None,
        todo_writer=None,
    )
    with pytest.raises(SessionResumeError, match="provider"):
        Session.build_resumed(session_id="ghost123", provider_arg=None,
                              ui_sinks=sinks, workspace=ws)


@pytest.mark.asyncio
async def test_agent_compose_loaded_memories_returns_layer(tmp_path: Path) -> None:
    """Agent.run prepends a transient system message carrying selected memory bodies."""
    from codey.memory.models import Memory
    from codey.memory.registry import MemoryRegistry

    prof = Provider(name="t", base_url="http://x", api_key="k", model="m",
                   context_window=200_000, max_output_tokens=8000,
                   compact_headroom=13000)
    reg = MemoryRegistry()
    reg._memories["always_x"] = Memory(
        name="always_x", description="d", type="t",
        body="ALWAYS_X_BODY_MARKER",
        created_at="t", updated_at="t", source_session="s",
        scope="global", source_path=tmp_path / "x.md",
    )

    async def fake_pick(*_a, **_k):
        return ["always_x"]

    agent = Agent(provider=prof, session_id="s",
                  _memory_registry=reg, _memory_select=fake_pick)
    layer = await agent._compose_loaded_memories("install x")
    assert layer is not None
    assert "ALWAYS_X_BODY_MARKER" in layer
    await agent.aclose()
