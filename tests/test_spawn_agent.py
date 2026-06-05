"""Tests for SpawnAgentTool and its end-to-end behavior with a parent Agent."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from codey.core import Agent, AssistantTextDelta, Message, ToolResult, TurnCompleted
from codey.core.agent import ToolRegistry
from codey.core.session import Session
from codey.config import Profile
from codey.tools.spawn_agent import SpawnAgentTool
from codey.ui.renderers import UISinks


def _session(tmp_path: Path) -> Session:
    sinks = UISinks(
        meta_writer=lambda _t: None,
        approve=None,
        todo_writer=None,
    )
    return Session.build(
        profile_arg=None,
        ui_sinks=sinks,
        workspace=tmp_path,
        otel_enabled=False,
    )


def _stub_child_to_return(text: str):
    """Return a fake _stream_one_round that emits `text` once, then stops."""
    async def fake(self):
        from codey.core.turn import _RoundDone
        yield AssistantTextDelta(text=text)
        yield _RoundDone(tool_calls=[])
    return fake


async def test_spawn_agent_returns_final_assistant_text(
    temp_config, tmp_path, monkeypatch
):
    sess = _session(tmp_path)
    try:
        tool = SpawnAgentTool(session_provider=lambda: sess)
        # Force every child's stream to emit a canned summary.
        monkeypatch.setattr(Agent, "_stream_one_round",
                            _stub_child_to_return("here is the summary"))
        result = await tool.run({"description": "probe", "prompt": "do the thing"})
        assert result == "here is the summary"
    finally:
        await sess.aclose()


async def test_spawn_agent_unknown_profile_returns_error(temp_config, tmp_path):
    sess = _session(tmp_path)
    try:
        tool = SpawnAgentTool(session_provider=lambda: sess)
        result = await tool.run({
            "description": "x", "prompt": "y", "profile": "ghost-profile",
        })
        assert result.startswith("error: unknown profile")
    finally:
        await sess.aclose()


async def test_spawn_agent_empty_prompt_returns_error(temp_config, tmp_path):
    sess = _session(tmp_path)
    try:
        tool = SpawnAgentTool(session_provider=lambda: sess)
        result = await tool.run({"description": "x", "prompt": ""})
        assert "non-empty" in result
    finally:
        await sess.aclose()


async def test_spawn_agent_child_error_reason_returns_error(
    temp_config, tmp_path, monkeypatch
):
    sess = _session(tmp_path)
    try:
        tool = SpawnAgentTool(session_provider=lambda: sess)

        async def fake_stream_raises(self):
            if False:  # make this an async generator
                yield
            raise RuntimeError("boom")

        monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_raises)
        result = await tool.run({"description": "x", "prompt": "y"})
        assert result.startswith("error: sub-agent failed:")
        assert "RuntimeError" in result
        assert "boom" in result
    finally:
        await sess.aclose()


async def test_spawn_agent_empty_final_message_returns_error(
    temp_config, tmp_path, monkeypatch
):
    sess = _session(tmp_path)
    try:
        tool = SpawnAgentTool(session_provider=lambda: sess)

        async def fake_stream_silent(self):
            from codey.core.turn import _RoundDone
            yield _RoundDone(tool_calls=[])

        monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_silent)
        result = await tool.run({"description": "x", "prompt": "y"})
        assert result == "error: sub-agent ended without a final message"
    finally:
        await sess.aclose()


async def test_spawn_agent_truncates_long_summaries(
    temp_config, tmp_path, monkeypatch
):
    sess = _session(tmp_path)
    try:
        tool = SpawnAgentTool(session_provider=lambda: sess)
        huge = "x" * 50_000
        monkeypatch.setattr(Agent, "_stream_one_round", _stub_child_to_return(huge))
        result = await tool.run({"description": "x", "prompt": "y"})
        assert len(result) < 11_000
        assert "truncated" in result
    finally:
        await sess.aclose()


async def test_spawn_agent_records_events_and_label(
    temp_config, tmp_path, monkeypatch
):
    sess = _session(tmp_path)
    try:
        tool = SpawnAgentTool(session_provider=lambda: sess)
        monkeypatch.setattr(Agent, "_stream_one_round",
                            _stub_child_to_return("summary"))

        _ = await tool.run({"description": "label", "prompt": "y"})

        children = sess.subagent_recorder.children()
        assert children == [f"{sess.session_id}.sub.1"]
        # Label was recorded so /subs can show it.
        assert sess.subagent_recorder.label_for(children[0]) == "label"
        # And the event stream was captured.
        events = sess.subagent_recorder.events_for(children[0])
        type_names = {type(e).__name__ for e in events}
        assert "TurnStarted" in type_names
        assert "AssistantMessageCompleted" in type_names
        assert "TurnCompleted" in type_names
    finally:
        await sess.aclose()


async def test_two_spawn_agents_run_concurrently(
    temp_config, tmp_path, monkeypatch
):
    """End-to-end concurrency: two spawn_agent calls in one parent turn run
    in parallel. Wall-clock < sum-of-children."""
    sess = _session(tmp_path)
    try:
        # Differentiate parent vs child Agents by their tool registry contents.
        # Parent has spawn_agent (registered by Session.build); children don't.
        async def slow_child_stream(self):
            await asyncio.sleep(0.3)
            from codey.core.turn import _RoundDone
            yield AssistantTextDelta(text="done")
            yield _RoundDone(tool_calls=[])

        async def parent_stream(self):
            if not getattr(self, "_seen", False):
                self._seen = True
                from codey.core.turn import _RoundDone
                yield _RoundDone(tool_calls=[
                    {"id": "c1", "type": "function", "function": {
                        "name": "spawn_agent",
                        "arguments": '{"description":"a","prompt":"do a"}'}},
                    {"id": "c2", "type": "function", "function": {
                        "name": "spawn_agent",
                        "arguments": '{"description":"b","prompt":"do b"}'}},
                ])
                return
            from codey.core.turn import _RoundDone
            yield AssistantTextDelta(text="ok")
            yield _RoundDone(tool_calls=[])

        def dispatch(self):
            if "spawn_agent" in self.tools.tools:
                return parent_stream(self)
            return slow_child_stream(self)

        monkeypatch.setattr(Agent, "_stream_one_round", dispatch)

        t0 = time.monotonic()
        events = [ev async for ev in sess.agent.run("go")]
        elapsed = time.monotonic() - t0

        # Two children, each sleeps 0.3s. Sequential would be 0.6s; parallel ~0.3s.
        assert elapsed < 0.5, f"expected < 0.5s, got {elapsed:.3f}s"

        results = [e for e in events if isinstance(e, ToolResult)]
        assert {r.id for r in results} == {"c1", "c2"}
        assert all(r.content == "done" for r in results)
    finally:
        await sess.aclose()
