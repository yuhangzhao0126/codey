"""Verify the CLI passes a todo writer that renders the list to stdout."""

from __future__ import annotations

from codey.tools.todo_write import Todo


def test_cli_todo_writer_renders_completed_with_strikethrough(capsys):
    from codey.cli import _make_todo_writer
    writer = _make_todo_writer()
    writer([
        Todo(1, "first",  "completed"),
        Todo(2, "second", "in_progress"),
        Todo(3, "third",  "pending"),
    ])
    out = capsys.readouterr().out
    assert "first" in out
    assert "second" in out
    assert "third" in out
    assert "\x1b[9m" in out      # ANSI strikethrough on completed
    assert "\x1b[2m" in out      # ANSI dim
    assert "tasks" in out.lower()


def test_cli_todo_writer_empty_list_renders_cleared(capsys):
    from codey.cli import _make_todo_writer
    writer = _make_todo_writer()
    writer([])
    out = capsys.readouterr().out
    assert "cleared" in out.lower()
