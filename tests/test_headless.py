"""Headless single-shot runner tests."""
from __future__ import annotations

import pytest

from codey.core.events import AssistantTextDelta, ToolCallRequested, ToolResult, TurnCompleted
from codey import headless as hl


class _FakeAgent:
    def __init__(self, events):
        self._events = events

    async def run(self, _prompt):
        for ev in self._events:
            yield ev


class _FakeSession:
    def __init__(self, events):
        self.agent = _FakeAgent(events)
        self.engine = type("E", (), {"mode": None})()

    async def aclose(self):
        pass


def _patch(monkeypatch, events):
    monkeypatch.setattr(hl.Session, "build", classmethod(lambda cls, **kw: _FakeSession(events)))


@pytest.mark.asyncio
async def test_stop_returns_zero_streams_text(monkeypatch, capsys):
    _patch(monkeypatch, [AssistantTextDelta(text="hi"), TurnCompleted(reason="stop")])
    rc = await hl.run_headless("x", provider_arg=None)
    assert rc == 0
    assert "hi" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_tool_trace_and_yolo(monkeypatch, capsys):
    sess = _FakeSession([ToolCallRequested(id="1", name="write_file", arguments={"path": "h"}),
                         ToolResult(id="1", name="write_file", ok=True, content="ok"),
                         TurnCompleted(reason="stop")])
    monkeypatch.setattr(hl.Session, "build", classmethod(lambda cls, **kw: sess))
    rc = await hl.run_headless("x", provider_arg=None)
    assert rc == 0
    assert sess.engine.mode == hl.Mode.YOLO
    assert "[tool] write_file" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_error_returns_one(monkeypatch):
    _patch(monkeypatch, [TurnCompleted(reason="error", error="boom")])
    assert await hl.run_headless("x", provider_arg=None) == 1
