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


def test_registered_in_default_registry():
    from codey.tools import build_default_registry
    reg = build_default_registry()
    assert "todo_write" in reg.tools
    assert isinstance(reg.tools["todo_write"], TodoWriteTool)


async def test_permission_hook_auto_allows_todo_write_in_all_modes():
    from codey.builtin_hooks.permission import permission_check_hook
    from codey.permissions import Mode, PermissionEngine
    for mode in (Mode.PARANOID, Mode.READ_ONLY, Mode.SAFE, Mode.YOLO):
        eng = PermissionEngine(mode=mode)
        hook = permission_check_hook(engine=eng, approve=None)
        result = await hook({
            "tool": "todo_write",
            "arguments": {"todos": []},
            "call_id": "x",
        })
        assert result is None, f"expected auto-allow in {mode.value}, got {result}"


async def test_agent_reset_clears_todo_list():
    from codey.agent import Agent
    from codey.config import Profile
    from codey.tools import build_default_registry

    reg = build_default_registry()
    todo_tool = reg.tools["todo_write"]
    await todo_tool.run({"todos": [{"content": "x", "status": "pending"}]})
    assert todo_tool.todos

    agent = Agent(
        profile=Profile(name="t", api_key="sk", base_url="http://x/v1", model="m"),
        system_prompt="",
        tools=reg,
    )
    agent.reset()
    assert todo_tool.todos == []
