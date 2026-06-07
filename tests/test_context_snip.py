"""Tests for snip_compact and the pair-boundary helpers."""
from __future__ import annotations

from codey.context.snip import (
    SNIP_KEEP_HEAD,
    SNIP_KEEP_TAIL,
    SNIP_THRESHOLD_MESSAGES,
    _expand_prefix_to_pair_boundary,
    _expand_suffix_to_pair_boundary,
    run as snip_run,
)
from codey.core.messages import Message


def _user(i: int) -> Message:
    return Message(role="user", content=f"u{i}")

def _asst(i: int) -> Message:
    return Message(role="assistant", content=f"a{i}")

def _call(call_id: str, fn: str = "bash") -> Message:
    return Message(role="assistant", content="", tool_calls=[
        {"id": call_id, "type": "function",
         "function": {"name": fn, "arguments": "{}"}},
    ])

def _result(call_id: str, fn: str = "bash") -> Message:
    return Message(role="tool", tool_call_id=call_id, name=fn, content=f"r{call_id}")


def test_under_threshold_is_no_op():
    body = [_user(i) for i in range(SNIP_THRESHOLD_MESSAGES)]
    hist = [Message(role="system", content="s")] + body
    metas = []
    n = snip_run(history=hist, meta=metas.append)
    assert n == 0
    assert metas == []
    assert len(hist) == SNIP_THRESHOLD_MESSAGES + 1


def test_just_over_threshold_snips_to_head_plus_marker_plus_tail():
    body = [_user(i) for i in range(SNIP_THRESHOLD_MESSAGES + 5)]
    hist = [Message(role="system", content="s")] + body
    metas = []
    n = snip_run(history=hist, meta=metas.append)
    assert n == 5
    assert len(hist) == 1 + SNIP_KEEP_HEAD + 1 + SNIP_KEEP_TAIL
    marker = hist[1 + SNIP_KEEP_HEAD]
    assert marker.role == "user"
    assert marker.content.startswith("[... 5 earlier message")
    assert "compacted by snip" in marker.content
    assert metas == ["[ctx: snipped 5 middle messages]"]


def test_expand_prefix_pulls_in_matching_tool_results():
    body = [
        _user(0), _user(1), _user(2), _user(3),
        _call("c1"),
        _result("c1"),
        _user(6),
    ]
    new_end = _expand_prefix_to_pair_boundary(body, 5)
    assert new_end == 6


def test_expand_prefix_no_op_when_no_pending_pair():
    body = [_user(0), _user(1), _user(2)]
    assert _expand_prefix_to_pair_boundary(body, 2) == 2


def test_expand_suffix_pulls_in_originating_assistant():
    body = [
        _user(0),
        _call("c1"),
        _result("c1"),
        _user(3),
    ]
    assert _expand_suffix_to_pair_boundary(body, 2) == 1


def test_expand_suffix_no_op_when_not_a_tool_message():
    body = [_user(0), _user(1), _user(2)]
    assert _expand_suffix_to_pair_boundary(body, 2) == 2


def test_snip_left_boundary_expands_when_inside_pair():
    head_block = [_user(0), _user(1), _user(2), _user(3), _call("c1"), _result("c1")]
    middle    = [_user(i) for i in range(10, 20)]
    tail      = [_user(i) for i in range(100, 145)]
    body = head_block + middle + tail
    hist = [Message(role="system", content="s")] + body
    n = snip_run(history=hist, meta=lambda _m: None)
    assert n == 10
    assert len(hist) == 1 + 6 + 1 + 45
    assert hist[6].role == "tool"


def test_snip_right_boundary_expands_when_inside_pair():
    head = [_user(i) for i in range(5)]
    # 9 middle + the assistant.tool_calls = boundary will land on its result.
    middle = [_user(i) for i in range(100, 109)] + [_call("ct")]
    tail = [_result("ct")] + [_user(i) for i in range(200, 244)]
    body = head + middle + tail
    # body length = 5 + 10 + 45 = 60. SNIP_KEEP_TAIL = 45 → suffix start
    # in body = 60 - 45 = 15, which IS the tool result. Expander pulls it
    # back to 14 (the assistant.tool_calls).
    hist = [Message(role="system", content="s")] + body
    n = snip_run(history=hist, meta=lambda _m: None)
    assert n == 9   # dropped 14 - 5 = 9
    # Final layout: system + 5 + marker + 46 = 53
    assert len(hist) == 1 + 5 + 1 + 46
    # The first tail message is the assistant.tool_calls now.
    assert hist[7].role == "assistant" and hist[7].tool_calls


def test_snip_no_op_when_windows_overlap_after_expansion():
    body = [_user(i) for i in range(4)] + [_call("c"), _result("c")] + \
           [_user(i) for i in range(100, 145)]
    hist = [Message(role="system", content="s")] + body
    n = snip_run(history=hist, meta=lambda _m: None)
    assert n == 0
    assert len(hist) == 52
