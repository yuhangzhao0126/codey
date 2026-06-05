"""Regression tests for concurrent tool dispatch within one round of Agent.run()."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from codey.core import (
    Agent,
    AssistantTextDelta,
    Message,
    ToolResult,
    TurnCompleted,
)
from codey.core.agent import ToolRegistry
from codey.config import Profile


def _agent_with_tools(*tools) -> Agent:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return Agent(
        profile=Profile(name="t", api_key="sk", base_url="http://x/v1", model="m"),
        system_prompt="",
        tools=reg,
    )


@dataclass
class SleepTool:
    """A tool that sleeps `delay` seconds and returns `payload`."""
    name: str
    description: str = "sleeps and returns payload"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object", "properties": {}, "additionalProperties": False,
    })
    delay: float = 0.3
    payload: str = "done"

    async def run(self, arguments: dict[str, Any]) -> str:
        await asyncio.sleep(self.delay)
        return self.payload


def _make_round_with_calls(*tool_specs):
    """Build a fake _stream_one_round that emits one round with the given tool calls.
    Each spec is a (call_id, tool_name) tuple. Returns an async generator function
    suitable for monkeypatching Agent._stream_one_round."""
    async def fake(self):
        # First call: emit the tool calls.
        if not getattr(self, "_test_round_seen", False):
            self._test_round_seen = True
            from codey.core.turn import _RoundDone
            yield _RoundDone(tool_calls=[
                {"id": cid, "type": "function",
                 "function": {"name": tname, "arguments": "{}"}}
                for cid, tname in tool_specs
            ])
            return
        # Second call (after tools dispatched): natural stop.
        yield AssistantTextDelta(text="ok")
        from codey.core.turn import _RoundDone
        yield _RoundDone(tool_calls=[])
    return fake


async def test_two_tools_run_concurrently(monkeypatch):
    """Two sleep tools in one round should finish in roughly max(delay), not sum(delay)."""
    agent = _agent_with_tools(
        SleepTool(name="slow_a", delay=0.3, payload="a-done"),
        SleepTool(name="slow_b", delay=0.3, payload="b-done"),
    )
    monkeypatch.setattr(
        Agent, "_stream_one_round",
        _make_round_with_calls(("call_a", "slow_a"), ("call_b", "slow_b")),
    )

    t0 = time.monotonic()
    events = [ev async for ev in agent.run("go")]
    elapsed = time.monotonic() - t0

    # Wall-clock proves concurrency: sequential would be ~0.6s, parallel ~0.3s.
    assert elapsed < 0.5, f"expected < 0.5s, got {elapsed:.3f}s"

    # Both tool messages appended to history.
    tool_msgs = [m for m in agent.history if m.role == "tool"]
    assert len(tool_msgs) == 2
    by_id = {m.tool_call_id: m.content for m in tool_msgs}
    assert by_id == {"call_a": "a-done", "call_b": "b-done"}

    # And the turn completed successfully.
    completed = [e for e in events if isinstance(e, TurnCompleted)]
    assert completed and completed[-1].reason == "stop"


async def test_tool_responses_match_by_id_not_position(monkeypatch):
    """When the slow tool finishes after the fast one, history may have them in
    finish order — but each tool_call_id still maps to its correct content."""
    agent = _agent_with_tools(
        SleepTool(name="slow", delay=0.3, payload="slow-content"),
        SleepTool(name="fast", delay=0.05, payload="fast-content"),
    )
    monkeypatch.setattr(
        Agent, "_stream_one_round",
        _make_round_with_calls(("call_slow", "slow"), ("call_fast", "fast")),
    )

    _ = [ev async for ev in agent.run("go")]

    tool_msgs = [m for m in agent.history if m.role == "tool"]
    by_id = {m.tool_call_id: m.content for m in tool_msgs}
    assert by_id == {"call_slow": "slow-content", "call_fast": "fast-content"}


async def test_concurrent_pre_hook_cancel_one_of_two(monkeypatch):
    """One tool's PRE hook cancels with a custom result; the other runs normally.
    Both produce ToolResult events and history entries."""
    from codey.hooks import HookEvent, HookResult

    agent = _agent_with_tools(
        SleepTool(name="ok_tool", delay=0.05, payload="ok-content"),
        SleepTool(name="blocked_tool", delay=0.05, payload="should-not-run"),
    )

    def pre_cancel_blocked(payload):
        if payload["tool"] == "blocked_tool":
            return HookResult(cancel=True, result="error: nope")
        return None

    agent.hooks.register(HookEvent.PRE_TOOL_USE, pre_cancel_blocked, name="test_cancel")

    monkeypatch.setattr(
        Agent, "_stream_one_round",
        _make_round_with_calls(("call_ok", "ok_tool"), ("call_blk", "blocked_tool")),
    )

    events = [ev async for ev in agent.run("go")]

    tool_msgs = [m for m in agent.history if m.role == "tool"]
    by_id = {m.tool_call_id: m.content for m in tool_msgs}
    assert by_id == {"call_ok": "ok-content", "call_blk": "error: nope"}

    results = [e for e in events if isinstance(e, ToolResult)]
    assert {(r.id, r.ok) for r in results} == {("call_ok", True), ("call_blk", False)}


async def test_concurrent_post_hook_rewrites_correct_message(monkeypatch):
    """Two tools both have a post-hook that rewrites the result. Each rewrite
    must land on the right history entry (not just history[-1]) even when the
    appends from the two _dispatch_one tasks interleave arbitrarily."""
    from codey.hooks import HookEvent, HookResult

    agent = _agent_with_tools(
        SleepTool(name="alpha", delay=0.05, payload="alpha-raw"),
        SleepTool(name="beta",  delay=0.05, payload="beta-raw"),
    )

    def rewrite_post(payload):
        return HookResult(modified_post_result=f"REWROTE-{payload['tool']}")

    agent.hooks.register(HookEvent.POST_TOOL_USE, rewrite_post, name="test_rewrite")

    monkeypatch.setattr(
        Agent, "_stream_one_round",
        _make_round_with_calls(("call_a", "alpha"), ("call_b", "beta")),
    )

    _ = [ev async for ev in agent.run("go")]

    tool_msgs = [m for m in agent.history if m.role == "tool"]
    by_id = {m.tool_call_id: m.content for m in tool_msgs}
    assert by_id == {"call_a": "REWROTE-alpha", "call_b": "REWROTE-beta"}
