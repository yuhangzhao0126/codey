"""Verify the TUI builds a todo writer that styles completed items."""

from __future__ import annotations


def test_tui_todo_writer_uses_dim_strike_on_completed():
    from codey.tools.todo_write import Todo
    from codey.tui import _make_tui_todo_writer

    captured = []
    writer = _make_tui_todo_writer(lambda line: captured.append(line))
    writer([
        Todo(1, "first",  "completed"),
        Todo(2, "second", "in_progress"),
        Todo(3, "third",  "pending"),
    ])
    text = "\n".join(captured)
    assert "[dim]" in text and "[strike]" in text
    assert "[bold]" in text
    assert "first" in text and "second" in text and "third" in text
    assert "tasks" in text.lower()


def test_tui_todo_writer_empty_list_renders_cleared_header():
    from codey.tui import _make_tui_todo_writer
    captured = []
    writer = _make_tui_todo_writer(lambda line: captured.append(line))
    writer([])
    assert captured
    assert "cleared" in "\n".join(captured).lower()
