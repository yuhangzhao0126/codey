"""Regression tests for history repair + broad error handling in Agent.run()."""

from __future__ import annotations

import json
from typing import Any

import pytest

from codey.core import (
    Agent,
    AssistantTextDelta,
    Message,
    TurnCompleted,
)
from codey.config import Profile


def _agent() -> Agent:
    """Build an Agent without touching the real API.
    We stub `_stream_one_round` per-test to control what comes back."""
    return Agent(
        profile=Profile(name="t", api_key="sk", base_url="http://x/v1", model="m"),
        system_prompt="",
    )


# ---------- _repair_history ----------

def test_repair_drops_orphan_assistant_tool_calls():
    agent = _agent()
    # baseline: system msg appended in __post_init__
    agent.history.append(Message(role="user", content="hi"))
    agent.history.append(Message(
        role="assistant", content="",
        tool_calls=[{"id": "call_1", "type": "function",
                     "function": {"name": "bash", "arguments": "{}"}}],
    ))
    # no matching tool message follows — exactly the broken state from the bug.
    agent._repair_history()
    roles = [m.role for m in agent.history]
    assert roles == ["system", "user"]


def test_repair_drops_assistant_when_some_tool_results_missing():
    agent = _agent()
    agent.history.append(Message(role="user", content="hi"))
    agent.history.append(Message(
        role="assistant", content="",
        tool_calls=[
            {"id": "call_1", "type": "function",
             "function": {"name": "bash", "arguments": "{}"}},
            {"id": "call_2", "type": "function",
             "function": {"name": "bash", "arguments": "{}"}},
        ],
    ))
    # Only one of two results present — still inconsistent.
    agent.history.append(Message(
        role="tool", content="ok", tool_call_id="call_1", name="bash"))
    agent._repair_history()
    roles = [m.role for m in agent.history]
    assert roles == ["system", "user"]


def test_repair_leaves_consistent_history_alone():
    agent = _agent()
    agent.history.append(Message(role="user", content="hi"))
    agent.history.append(Message(
        role="assistant", content="",
        tool_calls=[{"id": "c", "type": "function",
                     "function": {"name": "bash", "arguments": "{}"}}],
    ))
    agent.history.append(Message(
        role="tool", content="ok", tool_call_id="c", name="bash"))
    agent.history.append(Message(role="assistant", content="done"))
    before = list(agent.history)
    agent._repair_history()
    assert agent.history == before


def test_repair_drops_orphan_tool_block_with_no_preceding_assistant():
    agent = _agent()
    # Pathological: tool message at the end with no assistant.tool_calls before.
    agent.history.append(Message(role="user", content="hi"))
    agent.history.append(Message(
        role="tool", content="stray", tool_call_id="x", name="bash"))
    agent._repair_history()
    roles = [m.role for m in agent.history]
    assert roles == ["system", "user"]


# ---------- run() recovers from a poisoned history ----------

async def test_run_repairs_before_request(monkeypatch):
    agent = _agent()
    # poison history with a dangling assistant tool_calls (the bug we fixed).
    agent.history.append(Message(role="user", content="prior"))
    agent.history.append(Message(
        role="assistant", content="",
        tool_calls=[{"id": "ghost", "type": "function",
                     "function": {"name": "bash", "arguments": "{}"}}],
    ))

    seen_messages: list[list[dict]] = []

    async def fake_stream_one_round(self):
        # Snapshot the wire-format messages that would have been sent.
        seen_messages.append([m.to_wire() for m in self.history])
        yield AssistantTextDelta(text="ok")
        from codey.core.turn import _RoundDone
        yield _RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)

    events = [ev async for ev in agent.run("now do something")]
    completed = [e for e in events if isinstance(e, TurnCompleted)]
    assert completed and completed[-1].reason == "stop"

    # The request body must NOT contain the orphan assistant message.
    sent = seen_messages[0]
    roles_sent = [m["role"] for m in sent]
    # We expect: system, user(prior repaired-keeps), user(now), but the assistant
    # tool_call is gone. Actually `_repair_history` drops the assistant — and the
    # prior user message stays. Then we appended the new user message.
    assert roles_sent == ["system", "user", "user"]
    # Verify no tool_calls leaked through.
    assert not any("tool_calls" in m for m in sent)


# ---------- run() catches malformed-stream errors ----------

async def test_run_catches_jsondecodeerror_from_stream(monkeypatch):
    """A provider that sends a non-JSON error body used to crash the worker
    (OpenAI SDK raises json.JSONDecodeError, which wasn't in our except clause).
    Now we catch BaseException, roll back, and emit TurnCompleted(reason='error')."""
    agent = _agent()

    async def fake_stream_one_round(self):
        if False:  # make this an async generator
            yield
        raise json.JSONDecodeError("Expecting value", "Request Failed: 400 …", 0)

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)

    events = [ev async for ev in agent.run("hi")]
    completed = [e for e in events if isinstance(e, TurnCompleted)]
    assert completed and completed[-1].reason == "error"
    assert "JSONDecodeError" in (completed[-1].error or "")

    # And history was rolled back to the pre-turn baseline (no orphan user msg).
    roles = [m.role for m in agent.history]
    assert roles == ["system"]


async def test_run_catches_unexpected_runtime_error(monkeypatch):
    agent = _agent()

    async def fake_stream_one_round(self):
        if False:
            yield
        raise RuntimeError("kaboom")

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)

    events = [ev async for ev in agent.run("hi")]
    completed = [e for e in events if isinstance(e, TurnCompleted)]
    assert completed and completed[-1].reason == "error"
    assert "RuntimeError" in (completed[-1].error or "")
    assert "kaboom" in (completed[-1].error or "")
