"""Tests for the orchestrator that chains the 4 steps."""
from __future__ import annotations

import pytest

from codey.config import Provider
from codey.context import pipeline as pipeline_mod
from codey.context.pipeline import PARENT_THRESHOLDS, CHILD_THRESHOLDS, Thresholds
from codey.core.messages import Message

from _fake_openai import FakeClient


def _provider(window=100_000, headroom=13_000) -> Provider:
    return Provider(name="p", api_key="k", base_url="x", model="m",
                   context_window=window, max_output_tokens=4_096,
                   compact_headroom=headroom)


@pytest.mark.asyncio
async def test_run_proactive_small_history_is_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = [Message(role="system", content="s"),
            Message(role="user", content="hi")]
    metas = []
    await pipeline_mod.run_proactive(
        history=hist, provider=_provider(), session_id="sid",
        last_round_tool_idxs=[], meta=metas.append,
        client=FakeClient(response_text="x"), recent_files=[],
    )
    assert metas == []
    assert len(hist) == 2


@pytest.mark.asyncio
async def test_run_proactive_runs_budget_then_snip_then_micro(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = [Message(role="system", content="s")]
    for i in range(50):
        hist.append(Message(role="user", content=f"u{i}"))
    for i in range(10):
        hist.append(Message(role="tool", tool_call_id=f"c{i}", name="t",
                            content=f"body{i}"))
    hist[-2].content = "X" * 180_000
    hist[-1].content = "Y" * 30_000
    last_round_idxs = [len(hist) - 2, len(hist) - 1]

    metas = []
    await pipeline_mod.run_proactive(
        history=hist, provider=_provider(), session_id="sid",
        last_round_tool_idxs=last_round_idxs, meta=metas.append,
        client=FakeClient(response_text="x"), recent_files=[],
    )
    joined = "\n".join(metas)
    budget_pos = joined.find("persisted")
    snip_pos = joined.find("snipped")
    micro_pos = joined.find("replaced")
    assert 0 <= budget_pos < snip_pos < micro_pos


@pytest.mark.asyncio
async def test_run_proactive_triggers_llm_when_over_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    # context_window=10_000, max_output=4_096, headroom=1_000  →  threshold = 4_904
    hist = [Message(role="system", content="s"),
            Message(role="user", content="A" * 20_000)]
    metas = []
    await pipeline_mod.run_proactive(
        history=hist, provider=_provider(window=10_000, headroom=1_000),
        session_id="sid", last_round_tool_idxs=[], meta=metas.append,
        client=FakeClient(response_text="summary"), recent_files=[],
    )
    assert len(hist) == 2
    assert hist[1].role == "user"
    assert "Summary of prior conversation" in hist[1].content
    assert any("summarized history" in m for m in metas)


@pytest.mark.asyncio
async def test_run_proactive_force_summary_skips_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = [Message(role="system", content="s"),
            Message(role="user", content="tiny")]
    metas = []
    await pipeline_mod.run_proactive_force_summary(
        history=hist, provider=_provider(), session_id="sid",
        meta=metas.append, client=FakeClient(response_text="summary"),
        recent_files=[],
    )
    assert "Summary of prior conversation" in hist[1].content
    assert any("summarized history" in m for m in metas)


def test_child_thresholds_are_tighter():
    assert CHILD_THRESHOLDS.snip_threshold_messages < PARENT_THRESHOLDS.snip_threshold_messages
    assert CHILD_THRESHOLDS.snip_keep_head < PARENT_THRESHOLDS.snip_keep_head
    assert CHILD_THRESHOLDS.snip_keep_tail < PARENT_THRESHOLDS.snip_keep_tail


@pytest.mark.asyncio
async def test_run_reactive_delegates_to_reactive_run(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = [Message(role="system", content="s")] + \
           [Message(role="user", content=f"u{i}") for i in range(20)]
    await pipeline_mod.run_reactive(
        history=hist, provider=_provider(), session_id="sid",
        meta=lambda _m: None,
        client=FakeClient(response_text="reactive summary"), recent_files=[],
    )
    assert hist[0].role == "system"
    assert hist[1].role == "user"
    assert "Reactive compact triggered" in hist[1].content
