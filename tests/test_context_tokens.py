"""Tests for the chars/4 token estimator."""
from __future__ import annotations

from codey.context.tokens import estimate
from codey.core.messages import Message


def test_estimate_empty_history():
    assert estimate([]) == 0


def test_estimate_single_user_message():
    msgs = [Message(role="user", content="hello world")]    # 11 chars / 4 = 2
    assert estimate(msgs) == 2


def test_estimate_assistant_with_tool_calls():
    msgs = [
        Message(
            role="assistant",
            content="ok",                                    # 2
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},  # 4 + 12 = 16
            }],
        ),
    ]
    # content(2) + name(4) + arguments(12) = 18  →  18 // 4 = 4
    assert estimate(msgs) == 4


def test_estimate_tool_message_with_name():
    msgs = [Message(role="tool", tool_call_id="x", name="bash",
                    content="x" * 100)]
    # content(100) + name(4) = 104  →  26
    assert estimate(msgs) == 26


def test_estimate_sums_all_messages():
    msgs = [
        Message(role="system", content="x" * 40),    # 10
        Message(role="user", content="y" * 80),      # 20
        Message(role="assistant", content="z" * 40), # 10
    ]
    assert estimate(msgs) == 40


def test_estimate_handles_none_content():
    msgs = [Message(role="assistant", content="", tool_calls=[
        {"id": "1", "type": "function",
         "function": {"name": "t", "arguments": ""}}
    ])]
    # content(0) + name(1) + arguments(0) = 1  →  0
    assert estimate(msgs) == 0
