"""Tests for the model-callable `compact` tool."""
from __future__ import annotations

from collections import deque

import pytest

from codey.config import Provider
from codey.core.messages import Message
from codey.core.turn import Agent
from codey.tools.compact import CompactTool

from _fake_openai import FakeClient


def _provider():
    return Provider(name="p", api_key="k", base_url="x", model="m",
                   context_window=100_000, max_output_tokens=4_096,
                   compact_headroom=13_000)


@pytest.mark.asyncio
async def test_compact_tool_returns_canonical_string(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    agent = Agent(provider=_provider(), session_id="sid")
    agent.history.extend([Message(role="user", content="hi")])
    # Force the client to be our fake so the summary call doesn't go out.
    agent._client = FakeClient(response_text="summary text")

    class FakeSession:
        def __init__(self, ag):
            self.agent = ag
            self.session_id = ag.session_id

    sess = FakeSession(agent)
    tool = CompactTool(session_provider=lambda: sess)
    out = await tool.run({})
    assert out == "[Compacted. History summarized.]"
    # And history was actually compacted.
    assert agent.history[-1].role == "user"
    assert "Summary of prior conversation" in agent.history[-1].content


@pytest.mark.asyncio
async def test_compact_tool_errors_when_unwired():
    tool = CompactTool(session_provider=None)
    out = await tool.run({})
    assert out.startswith("error:")


def test_compact_tool_schema_has_no_required_params():
    tool = CompactTool(session_provider=None)
    assert tool.name == "compact"
    assert tool.parameters["properties"] == {}
    assert "required" not in tool.parameters or not tool.parameters["required"]
