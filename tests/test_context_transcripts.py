"""Tests for tool-result persistence and history snapshot writers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codey.context.transcripts import (
    persisted_root_for,
    snapshots_root_for,
    write_persisted_tool_result,
    write_history_snapshot,
)
from codey.core.messages import Message


def test_persisted_root_layout(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = persisted_root_for("abc12345")
    assert p == tmp_path / "transcripts" / "abc12345" / "tool_results"


def test_snapshots_root_layout(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = snapshots_root_for("abc12345")
    assert p == tmp_path / "transcripts" / "abc12345" / "snapshots"


def test_write_persisted_tool_result_creates_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = write_persisted_tool_result(
        session_id="sess1", call_id="call_x", tool_name="bash",
        body="hello world",
    )
    assert p.read_text() == "hello world"
    assert p.parent == tmp_path / "transcripts" / "sess1" / "tool_results"
    assert p.name == "call_x-bash.txt"


def test_write_persisted_tool_result_sanitizes_call_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = write_persisted_tool_result(
        session_id="s", call_id="weird/call:id", tool_name="ba sh",
        body="x",
    )
    assert "/" not in p.name
    assert ":" not in p.name
    assert " " not in p.name


def test_write_history_snapshot_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    history = [
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello",
                tool_calls=[{"id": "c1", "type": "function",
                             "function": {"name": "t", "arguments": "{}"}}]),
        Message(role="tool", tool_call_id="c1", name="t", content="ok"),
    ]
    p = write_history_snapshot(session_id="sess2", history=history, kind="proactive")
    assert p.exists()
    data = json.loads(p.read_text())
    assert isinstance(data, list)
    assert len(data) == 4
    assert data[0] == {"role": "system", "content": "sys"}
    assert data[2]["tool_calls"][0]["id"] == "c1"
    assert data[3]["tool_call_id"] == "c1"
    assert "proactive" in p.name


def test_write_history_snapshot_kind_in_filename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = write_history_snapshot(session_id="s", history=[], kind="reactive")
    assert "reactive" in p.name
    assert p.name.endswith(".json")
