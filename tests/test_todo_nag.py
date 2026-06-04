"""Tests for the todo_nag hook trio."""

from __future__ import annotations

import pytest

from codey.builtin_hooks.todo_nag import build_todo_nag_hooks


async def test_no_nag_below_threshold():
    pre, post, stop = build_todo_nag_hooks(threshold=3)
    for _ in range(3):
        pre({"tool": "bash", "arguments": {}, "call_id": "x"})
        result = post({"tool": "bash", "arguments": {}, "call_id": "x",
                       "ok": True, "result": "ok"})
        assert result is None or result.modified_post_result is None


async def test_nag_after_threshold():
    pre, post, stop = build_todo_nag_hooks(threshold=3)
    for i in range(3):
        pre({"tool": "bash", "arguments": {}, "call_id": "x"})
        post({"tool": "bash", "arguments": {}, "call_id": "x",
              "ok": True, "result": "ok"})
    pre({"tool": "bash", "arguments": {}, "call_id": "x"})
    result = post({"tool": "bash", "arguments": {}, "call_id": "x",
                   "ok": True, "result": "ok"})
    assert result is not None
    assert result.modified_post_result is not None
    assert "todo_write" in result.modified_post_result
    assert result.modified_post_result.startswith("ok")


async def test_nag_fires_at_most_once_per_turn():
    pre, post, stop = build_todo_nag_hooks(threshold=3)
    nags = 0
    for _ in range(10):
        pre({"tool": "bash", "arguments": {}, "call_id": "x"})
        r = post({"tool": "bash", "arguments": {}, "call_id": "x",
                  "ok": True, "result": "ok"})
        if r is not None and r.modified_post_result is not None:
            nags += 1
    assert nags == 1


async def test_todo_write_resets_counter():
    pre, post, stop = build_todo_nag_hooks(threshold=3)
    for _ in range(3):
        pre({"tool": "bash", "arguments": {}, "call_id": "x"})
        post({"tool": "bash", "arguments": {}, "call_id": "x",
              "ok": True, "result": "ok"})
    pre({"tool": "todo_write", "arguments": {}, "call_id": "y"})
    post({"tool": "todo_write", "arguments": {}, "call_id": "y",
          "ok": True, "result": "todo list updated"})
    nags = 0
    for _ in range(3):
        pre({"tool": "bash", "arguments": {}, "call_id": "z"})
        r = post({"tool": "bash", "arguments": {}, "call_id": "z",
                  "ok": True, "result": "ok"})
        if r is not None and r.modified_post_result is not None:
            nags += 1
    assert nags == 0


async def test_stop_resets_per_turn_state():
    pre, post, stop = build_todo_nag_hooks(threshold=3)
    for _ in range(4):
        pre({"tool": "bash", "arguments": {}, "call_id": "x"})
        post({"tool": "bash", "arguments": {}, "call_id": "x",
              "ok": True, "result": "ok"})
    stop({"reason": "stop", "error": None})
    nags = 0
    for _ in range(4):
        pre({"tool": "bash", "arguments": {}, "call_id": "x"})
        r = post({"tool": "bash", "arguments": {}, "call_id": "x",
                  "ok": True, "result": "ok"})
        if r is not None and r.modified_post_result is not None:
            nags += 1
    assert nags == 1
