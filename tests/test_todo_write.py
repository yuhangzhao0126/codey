"""Tests for the todo_write tool."""

from __future__ import annotations

import pytest

from codey.tools.todo_write import Todo, TodoWriteTool


async def test_basic_replace_assigns_ids():
    tool = TodoWriteTool()
    out = await tool.run({"todos": [
        {"content": "step one", "status": "pending"},
        {"content": "step two", "status": "in_progress"},
    ]})
    assert "2" in out
    assert [t.id for t in tool.todos] == [1, 2]
    assert tool.todos[0].content == "step one"
    assert tool.todos[1].status == "in_progress"


async def test_replace_overwrites_previous_state():
    tool = TodoWriteTool()
    await tool.run({"todos": [{"content": "a", "status": "pending"}]})
    await tool.run({"todos": [
        {"content": "b", "status": "completed"},
        {"content": "c", "status": "pending"},
    ]})
    assert [t.content for t in tool.todos] == ["b", "c"]
    assert [t.id for t in tool.todos] == [1, 2]


async def test_empty_list_clears():
    tool = TodoWriteTool()
    await tool.run({"todos": [{"content": "x", "status": "pending"}]})
    await tool.run({"todos": []})
    assert tool.todos == []


async def test_rejects_bad_status():
    tool = TodoWriteTool()
    out = await tool.run({"todos": [{"content": "x", "status": "wat"}]})
    assert out.startswith("error:")
    assert tool.todos == []


async def test_rejects_empty_content():
    tool = TodoWriteTool()
    out = await tool.run({"todos": [{"content": "", "status": "pending"}]})
    assert out.startswith("error:")


async def test_rejects_too_many_in_progress():
    tool = TodoWriteTool()
    out = await tool.run({"todos": [
        {"content": "a", "status": "in_progress"},
        {"content": "b", "status": "in_progress"},
    ]})
    assert out.startswith("error:")
    assert "in_progress" in out
    assert tool.todos == []


async def test_rejects_too_many_items():
    tool = TodoWriteTool()
    out = await tool.run({"todos": [
        {"content": f"item {i}", "status": "pending"} for i in range(51)
    ]})
    assert out.startswith("error:")
    assert "50" in out


async def test_rejects_non_list():
    tool = TodoWriteTool()
    out = await tool.run({"todos": "not a list"})
    assert out.startswith("error:")


async def test_schema_shape():
    tool = TodoWriteTool()
    p = tool.parameters
    assert p["type"] == "object"
    assert "todos" in p["properties"]
    item_schema = p["properties"]["todos"]["items"]
    assert set(item_schema["properties"]["status"]["enum"]) == {
        "pending", "in_progress", "completed",
    }
