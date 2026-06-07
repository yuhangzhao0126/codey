"""Tests for the recent_reads PostToolUse hook."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from codey.hooks.builtin.recent_reads import build_recent_reads_hook


@pytest.mark.asyncio
async def test_records_successful_read_file():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    hook({
        "tool": "read_file",
        "arguments": {"path": "/tmp/a.txt"},
        "ok": True,
        "result": "...",
        "call_id": "c1",
    })
    assert list(dq) == [Path("/tmp/a.txt")]


@pytest.mark.asyncio
async def test_ignores_other_tools():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    hook({"tool": "bash", "arguments": {"command": "ls"},
          "ok": True, "result": "...", "call_id": "c"})
    assert list(dq) == []


@pytest.mark.asyncio
async def test_ignores_failed_read_file():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    hook({"tool": "read_file", "arguments": {"path": "/tmp/x"},
          "ok": False, "result": "error", "call_id": "c"})
    assert list(dq) == []


@pytest.mark.asyncio
async def test_dedupes_by_path():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    for p in ("a", "b", "a", "c"):
        hook({"tool": "read_file", "arguments": {"path": p},
              "ok": True, "result": "...", "call_id": "c"})
    assert [str(x) for x in dq] == ["b", "a", "c"]


@pytest.mark.asyncio
async def test_respects_maxlen():
    dq: deque = deque(maxlen=3)
    hook = build_recent_reads_hook(dq)
    for p in ("a", "b", "c", "d", "e"):
        hook({"tool": "read_file", "arguments": {"path": p},
              "ok": True, "result": "...", "call_id": "c"})
    assert [str(x) for x in dq] == ["c", "d", "e"]


@pytest.mark.asyncio
async def test_skips_missing_path_arg():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    hook({"tool": "read_file", "arguments": {},
          "ok": True, "result": "...", "call_id": "c"})
    assert list(dq) == []
