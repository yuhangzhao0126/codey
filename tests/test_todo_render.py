"""Tests for the todo_render hook."""

from __future__ import annotations

from codey.hooks.builtin.todo_render import todo_render_hook
from codey.tools.todo_write import Todo, TodoWriteTool


def test_renders_only_on_todo_write():
    tool = TodoWriteTool()
    captured = []
    hook = todo_render_hook(tool=tool, writer=lambda todos: captured.append(list(todos)))
    hook({"tool": "bash", "ok": True, "result": "ok"})
    assert captured == []


def test_renders_current_list_after_todo_write():
    tool = TodoWriteTool()
    tool.todos = [
        Todo(1, "first",  "completed"),
        Todo(2, "second", "in_progress"),
        Todo(3, "third",  "pending"),
    ]
    captured = []
    hook = todo_render_hook(tool=tool, writer=lambda todos: captured.append(list(todos)))
    hook({"tool": "todo_write", "ok": True, "result": "todo list updated: 3 item(s)"})
    assert len(captured) == 1
    rendered = captured[0]
    assert [t.content for t in rendered] == ["first", "second", "third"]
    assert [t.status for t in rendered] == ["completed", "in_progress", "pending"]


def test_skips_render_on_failed_call():
    tool = TodoWriteTool()
    captured = []
    hook = todo_render_hook(tool=tool, writer=lambda todos: captured.append(list(todos)))
    hook({"tool": "todo_write", "ok": False, "result": "error: bad"})
    assert captured == []


def test_writer_errors_are_swallowed():
    tool = TodoWriteTool()
    tool.todos = [Todo(1, "x", "pending")]
    def boom(_): raise RuntimeError("ui blew up")
    hook = todo_render_hook(tool=tool, writer=boom)
    hook({"tool": "todo_write", "ok": True, "result": "todo list updated"})


def test_build_default_hooks_registers_todo_hooks():
    from codey.hooks.builtin import build_default_hooks
    from codey.hooks import HookEvent
    from codey.permissions import PermissionEngine

    tool = TodoWriteTool()
    reg = build_default_hooks(
        engine=PermissionEngine(),
        approve=None,
        transcript_writer=lambda style, text: None,
        meta_writer=lambda text: None,
        todo_tool=tool,
        todo_writer=lambda todos: None,
    )
    names_pre = {h.name for h in reg.list(HookEvent.PRE_TOOL_USE)}
    names_post = {h.name for h in reg.list(HookEvent.POST_TOOL_USE)}
    names_stop = {h.name for h in reg.list(HookEvent.STOP)}
    assert "todo_nag_pre" in names_pre
    assert "todo_nag_post" in names_post
    assert "todo_render" in names_post
    assert "todo_nag_stop" in names_stop


def test_build_default_hooks_omits_todo_hooks_without_tool():
    from codey.hooks.builtin import build_default_hooks
    from codey.hooks import HookEvent
    from codey.permissions import PermissionEngine

    reg = build_default_hooks(
        engine=PermissionEngine(),
        approve=None,
        transcript_writer=lambda style, text: None,
        meta_writer=lambda text: None,
    )
    names = {h.name for h in reg.list()}
    assert "todo_render" not in names
    assert "todo_nag_pre" not in names
