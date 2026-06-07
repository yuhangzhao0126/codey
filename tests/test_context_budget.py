"""Tests for tool_result_budget — the disk-spill step for big tool outputs."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.context.budget import (
    PERSISTED_STUB_PREFIX,
    TOOL_RESULT_BUDGET_BYTES,
    TOOL_RESULT_PERSIST_PREVIEW_CHARS,
    run as budget_run,
)
from codey.core.messages import Message


def _hist():
    return [
        Message(role="system", content="sys"),
        Message(role="user", content="do stuff"),
        Message(role="assistant", content="", tool_calls=[
            {"id": "c1", "type": "function",
             "function": {"name": "bash", "arguments": "{}"}},
            {"id": "c2", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}},
        ]),
        Message(role="tool", tool_call_id="c1", name="bash", content="small"),
        Message(role="tool", tool_call_id="c2", name="read_file", content="also small"),
    ]


def test_no_idxs_is_no_op(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    metas = []
    n = budget_run(history=hist, last_round_tool_idxs=[],
                   session_id="s", meta=metas.append)
    assert n == 0
    assert metas == []


def test_under_threshold_is_no_op(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4],
                   session_id="s", meta=lambda _m: None)
    assert n == 0
    assert hist[3].content == "small"
    assert hist[4].content == "also small"


def test_over_threshold_persists_all_round_tool_results(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    hist[3].content = "A" * 180_000
    hist[4].content = "B" * 30_000
    hist.append(Message(role="tool", tool_call_id="c3", name="grep", content="C" * 10_000))
    metas = []
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4, 5],
                   session_id="sess", meta=metas.append)
    assert n == 3
    for idx in (3, 4, 5):
        assert hist[idx].content.startswith(PERSISTED_STUB_PREFIX)
        assert "path:" in hist[idx].content
        assert "original_bytes:" in hist[idx].content
    bucket = tmp_path / "transcripts" / "sess" / "tool_results"
    assert sorted(p.name for p in bucket.iterdir()) == [
        "c1-bash.txt", "c2-read_file.txt", "c3-grep.txt",
    ]
    assert len(metas) == 1
    assert "persisted 3 tool result" in metas[0]
    assert "kb)" in metas[0]


def test_preview_truncated_to_constant(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    hist[3].content = "X" * 250_000
    hist[4].content = "Y" * 10_000
    budget_run(history=hist, last_round_tool_idxs=[3, 4],
               session_id="s", meta=lambda _m: None)
    stub = hist[3].content
    assert "X" * TOOL_RESULT_PERSIST_PREVIEW_CHARS in stub
    assert "X" * (TOOL_RESULT_PERSIST_PREVIEW_CHARS + 1) not in stub


def test_idempotent_on_second_run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    hist[3].content = "A" * 250_000
    hist[4].content = "B" * 10_000
    budget_run(history=hist, last_round_tool_idxs=[3, 4],
               session_id="s", meta=lambda _m: None)
    first_stub = hist[3].content
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4],
                   session_id="s", meta=lambda _m: None)
    assert n == 0
    assert hist[3].content == first_stub


def test_persist_failure_leaves_message_unchanged(tmp_path: Path, monkeypatch):
    bad = tmp_path / "x"
    bad.write_text("not a dir")
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", bad)
    hist = _hist()
    hist[3].content = "A" * 250_000
    hist[4].content = "B" * 10_000
    metas = []
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4],
                   session_id="s", meta=metas.append)
    assert n == 0
    assert hist[3].content == "A" * 250_000
    assert any("persist failed" in m for m in metas)


def test_threshold_constant_is_200kb():
    assert TOOL_RESULT_BUDGET_BYTES == 200_000
