"""Tests for the subagent_render hook (parent-only meta lines)."""

from __future__ import annotations

from codey.hooks.builtin.subagent_render import build_subagent_render_hooks


def test_pre_emits_start_line_with_label():
    lines: list[str] = []
    pre, _ = build_subagent_render_hooks(lines.append)
    pre({"tool": "spawn_agent", "call_id": "c1",
         "arguments": {"description": "investigate-auth"}})
    assert lines == ['⏵ sub-agent[1] "investigate-auth"']


def test_concurrent_indices_increment_in_call_order():
    lines: list[str] = []
    pre, _ = build_subagent_render_hooks(lines.append)
    pre({"tool": "spawn_agent", "call_id": "a",
         "arguments": {"description": "first"}})
    pre({"tool": "spawn_agent", "call_id": "b",
         "arguments": {"description": "second"}})
    assert lines == [
        '⏵ sub-agent[1] "first"',
        '⏵ sub-agent[2] "second"',
    ]


def test_post_emits_done_line_with_elapsed_for_ok_result():
    lines: list[str] = []
    pre, post = build_subagent_render_hooks(lines.append)
    pre({"tool": "spawn_agent", "call_id": "c1",
         "arguments": {"description": "probe"}})
    post({"tool": "spawn_agent", "call_id": "c1",
          "ok": True, "result": "fine"})
    assert lines[0].startswith("⏵ sub-agent[1]")
    assert lines[1].startswith("⏷ sub-agent[1] done (")


def test_post_emits_failed_line_when_result_is_error_string():
    lines: list[str] = []
    pre, post = build_subagent_render_hooks(lines.append)
    pre({"tool": "spawn_agent", "call_id": "c1",
         "arguments": {"description": "probe"}})
    post({"tool": "spawn_agent", "call_id": "c1",
          "ok": True, "result": "error: sub-agent failed: boom"})
    assert lines[-1].startswith("⏷ sub-agent[1] failed (")
    assert "boom" in lines[-1]


def test_post_emits_failed_line_when_ok_is_false():
    lines: list[str] = []
    pre, post = build_subagent_render_hooks(lines.append)
    pre({"tool": "spawn_agent", "call_id": "c1",
         "arguments": {"description": "probe"}})
    post({"tool": "spawn_agent", "call_id": "c1",
          "ok": False, "result": "denied"})
    assert lines[-1].startswith("⏷ sub-agent[1] failed (")


def test_hook_ignores_other_tools():
    lines: list[str] = []
    pre, post = build_subagent_render_hooks(lines.append)
    pre({"tool": "bash", "call_id": "x", "arguments": {"command": "ls"}})
    post({"tool": "bash", "call_id": "x", "ok": True, "result": "out"})
    assert lines == []


def test_unknown_call_id_in_post_is_silent():
    """A POST for a call_id we never saw (e.g. PRE was cancelled / dropped)
    should not blow up — just stay quiet."""
    lines: list[str] = []
    _, post = build_subagent_render_hooks(lines.append)
    post({"tool": "spawn_agent", "call_id": "ghost",
          "ok": True, "result": "fine"})
    assert lines == []


def test_missing_description_falls_back_to_placeholder():
    lines: list[str] = []
    pre, _ = build_subagent_render_hooks(lines.append)
    pre({"tool": "spawn_agent", "call_id": "c1", "arguments": {}})
    assert lines == ['⏵ sub-agent[1] "(no description)"']
