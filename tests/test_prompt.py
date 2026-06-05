"""Tests for system prompt assembly (parent + sub-agent layers)."""

from __future__ import annotations

from codey.prompt import build_subagent_system_prompt


def test_subagent_prompt_includes_default_section():
    """The default child prompt should include the four load-bearing statements:
    fresh context, only-final-message-returned, no spawn_agent, no todo_write."""
    prompt = build_subagent_system_prompt(description="investigate auth")

    # The description is the parent's per-call label; it should appear so the
    # child knows what it was asked to do at a glance.
    assert "investigate auth" in prompt

    # The four invariants the child must understand.
    text = prompt.lower()
    assert "sub-agent" in text or "subagent" in text
    assert "final" in text and "message" in text       # only final message returned
    assert "spawn_agent" in text                        # explicitly omitted
    assert "todo_write" in text                         # explicitly omitted
