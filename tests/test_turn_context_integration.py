"""End-to-end tests that Agent.run() invokes the context pipeline at
the top of each round and the reactive path on PromptTooLongError."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from codey.config import Profile
from codey.context.errors import PromptTooLongError
from codey.core.events import (
    AssistantMessageCompleted, AssistantTextDelta, TurnCompleted,
)
from codey.core.messages import Message
from codey.core.streaming import RoundDone
from codey.core.turn import Agent


def _profile():
    return Profile(name="p", api_key="k", base_url="https://x",
                   model="m", context_window=100_000,
                   max_output_tokens=4_096, compact_headroom=13_000)


@pytest.mark.asyncio
async def test_run_invokes_pipeline_at_top_of_round(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    calls = []

    async def fake_run_proactive(**kwargs):
        calls.append(("proactive", len(kwargs["history"])))

    monkeypatch.setattr("codey.context.run_proactive", fake_run_proactive)
    monkeypatch.setattr("codey.core.turn.context_pipeline.run_proactive", fake_run_proactive)

    agent = Agent(profile=_profile())

    async def fake_stream(self):
        yield AssistantTextDelta(text="hi")
        yield RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("hello"):
        events.append(ev)

    assert any(c[0] == "proactive" for c in calls)
    assert any(isinstance(e, TurnCompleted) and e.reason == "stop" for e in events)


@pytest.mark.asyncio
async def test_run_retries_once_on_prompt_too_long(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    reactive_called = []

    async def fake_reactive(**kwargs):
        reactive_called.append(True)

    monkeypatch.setattr("codey.core.turn.context_pipeline.run_reactive", fake_reactive)

    agent = Agent(profile=_profile())

    fail_count = {"n": 0}

    async def fake_stream(self):
        fail_count["n"] += 1
        if fail_count["n"] == 1:
            raise PromptTooLongError("too big")
        yield AssistantTextDelta(text="ok")
        yield RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("hello"):
        events.append(ev)

    assert reactive_called == [True]
    assert fail_count["n"] == 2
    assert any(isinstance(e, TurnCompleted) and e.reason == "stop" for e in events)


@pytest.mark.asyncio
async def test_run_surfaces_error_after_one_reactive_retry(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    async def fake_reactive(**kwargs):
        pass

    monkeypatch.setattr("codey.core.turn.context_pipeline.run_reactive", fake_reactive)

    agent = Agent(profile=_profile())

    async def fake_stream(self):
        raise PromptTooLongError("still too big")
        yield

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("hello"):
        events.append(ev)
    last = events[-1]
    assert isinstance(last, TurnCompleted)
    assert last.reason == "error"
    assert "PromptTooLong" in (last.error or "")


@pytest.mark.asyncio
async def test_run_breaks_round_loop_when_only_compact_called(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    async def noop(**kwargs):
        pass

    monkeypatch.setattr("codey.core.turn.context_pipeline.run_proactive", noop)

    agent = Agent(profile=_profile())

    # Register a fake compact tool so dispatch finds it.
    class FakeCompact:
        name = "compact"
        description = ""
        parameters = {"type": "object", "properties": {}}
        async def run(self, args):
            return "[Compacted. History summarized.]"
    agent.tools.register(FakeCompact())

    round_count = {"n": 0}

    async def fake_stream(self):
        round_count["n"] += 1
        if round_count["n"] == 1:
            yield AssistantTextDelta(text="ok let me compact")
            yield RoundDone(tool_calls=[{
                "id": "c1", "type": "function",
                "function": {"name": "compact", "arguments": "{}"},
            }])
        else:
            yield AssistantTextDelta(text="more")
            yield RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("compact please"):
        events.append(ev)
    # Should have run exactly ONE round and then ended the turn.
    assert round_count["n"] == 1
    assert any(isinstance(e, TurnCompleted) and e.reason == "stop" for e in events)
