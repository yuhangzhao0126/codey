"""Tests for micro_compact — placeholder all but the last 5 tool results."""
from __future__ import annotations

from codey.context.micro import (
    MICRO_KEEP_RECENT_TOOL_RESULTS,
    PLACEHOLDER,
    run as micro_run,
)
from codey.core.messages import Message


def _tool(call_id: str, content: str = "x") -> Message:
    return Message(role="tool", tool_call_id=call_id, name="t", content=content)


def test_under_threshold_is_no_op():
    hist = [Message(role="system", content="s")] + [_tool(f"c{i}", "x") for i in range(5)]
    n = micro_run(history=hist, meta=lambda _m: None)
    assert n == 0
    for m in hist[1:]:
        assert m.content == "x"


def test_over_threshold_replaces_all_but_last_5():
    tools = [_tool(f"c{i}", f"body{i}") for i in range(10)]
    hist = [Message(role="system", content="s")] + tools
    metas = []
    n = micro_run(history=hist, meta=metas.append)
    assert n == 5
    for i in range(5):
        assert hist[1 + i].content == PLACEHOLDER
    for i in range(5, 10):
        assert hist[1 + i].content == f"body{i}"
    assert metas == ["[ctx: replaced 5 old tool results with placeholder]"]


def test_idempotent_second_run():
    tools = [_tool(f"c{i}", f"body{i}") for i in range(8)]
    hist = [Message(role="system", content="s")] + tools
    micro_run(history=hist, meta=lambda _m: None)
    metas = []
    n = micro_run(history=hist, meta=metas.append)
    assert n == 0
    assert metas == []


def test_interleaved_messages_are_skipped():
    hist = [
        Message(role="system", content="s"),
        Message(role="user", content="u0"),
        _tool("c0", "body0"),
        Message(role="assistant", content="a0"),
        _tool("c1", "body1"),
        _tool("c2", "body2"),
        Message(role="user", content="u1"),
        _tool("c3", "body3"),
        _tool("c4", "body4"),
        _tool("c5", "body5"),
        _tool("c6", "body6"),
        _tool("c7", "body7"),
    ]
    n = micro_run(history=hist, meta=lambda _m: None)
    assert n == 3
    assert hist[2].content == PLACEHOLDER
    assert hist[4].content == PLACEHOLDER
    assert hist[5].content == PLACEHOLDER
    assert hist[7].content == "body3"
    assert hist[11].content == "body7"


def test_keep_constant_is_five():
    assert MICRO_KEEP_RECENT_TOOL_RESULTS == 5
