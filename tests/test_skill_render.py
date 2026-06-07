"""Tests for the skill_render hook — meta line per load_skill call."""

from __future__ import annotations

from codey.hooks.builtin.skill_render import build_skill_render_hook


def test_emits_success_line_on_ok_result():
    lines: list[str] = []
    hook = build_skill_render_hook(lines.append)
    hook({"tool": "load_skill", "call_id": "c1",
          "arguments": {"name": "code-review"},
          "ok": True, "result": "# Code review skill\n..."})
    assert lines == ["↳ skill loaded: code-review"]


def test_emits_error_line_when_result_is_error_string():
    lines: list[str] = []
    hook = build_skill_render_hook(lines.append)
    hook({"tool": "load_skill", "call_id": "c1",
          "arguments": {"name": "nope"},
          "ok": True,
          "result": "error: no skill named 'nope'. Available: code-review"})
    assert lines == ["✗ skill load failed: nope — no skill named 'nope'"]


def test_emits_error_line_when_ok_false():
    lines: list[str] = []
    hook = build_skill_render_hook(lines.append)
    hook({"tool": "load_skill", "call_id": "c1",
          "arguments": {"name": "x"},
          "ok": False, "result": "denied"})
    assert lines[0].startswith("✗ skill load failed: x")


def test_ignores_other_tools():
    lines: list[str] = []
    hook = build_skill_render_hook(lines.append)
    hook({"tool": "read_file", "call_id": "x",
          "arguments": {"path": "/tmp/foo"},
          "ok": True, "result": "..."})
    hook({"tool": "bash", "call_id": "y",
          "arguments": {"command": "ls"},
          "ok": True, "result": "..."})
    assert lines == []


def test_missing_name_falls_back_to_placeholder():
    lines: list[str] = []
    hook = build_skill_render_hook(lines.append)
    hook({"tool": "load_skill", "call_id": "c1",
          "arguments": {},
          "ok": True, "result": "body"})
    assert lines == ["↳ skill loaded: (unknown)"]
