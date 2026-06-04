"""Tests for the hook system."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from codey.core import (
    Agent,
    AssistantTextDelta,
    Message,
    ToolRegistry,
    TurnCompleted,
)
from codey.core.turn import _RoundDone
from codey.hooks.builtin.audit_log import audit_log_hook
from codey.hooks.builtin.permission import permission_check_hook
from codey.hooks.builtin.stop_logger import stop_logger_hook
from codey.hooks.builtin.transcript import (
    post_tool_render_hook,
    pre_tool_render_hook,
)
from codey.config import Profile
from codey.hooks import Hook, HookEvent, HookRegistry, HookResult
from codey.permissions import Mode, PermissionEngine, Verdict


# ---------- HookRegistry basics ----------

def test_register_and_list():
    reg = HookRegistry()
    reg.register(HookEvent.PRE_TOOL_USE, lambda p: None, name="a")
    reg.register(HookEvent.POST_TOOL_USE, lambda p: None, name="b")
    names = sorted(h.name for h in reg.list())
    assert names == ["a", "b"]
    assert [h.name for h in reg.list(HookEvent.PRE_TOOL_USE)] == ["a"]


def test_duplicate_name_raises():
    reg = HookRegistry()
    reg.register(HookEvent.STOP, lambda p: None, name="x")
    with pytest.raises(ValueError):
        reg.register(HookEvent.STOP, lambda p: None, name="x")


def test_enable_disable_unregister():
    reg = HookRegistry()
    reg.register(HookEvent.STOP, lambda p: None, name="x")
    assert reg.disable("x") is True
    assert reg.list(HookEvent.STOP)[0].enabled is False
    assert reg.enable("x") is True
    assert reg.list(HookEvent.STOP)[0].enabled is True
    assert reg.unregister("x") is True
    assert reg.list(HookEvent.STOP) == []


async def test_trigger_calls_hooks_in_order():
    reg = HookRegistry()
    order = []
    reg.register(HookEvent.STOP, lambda p: order.append("a") or None, name="a")
    reg.register(HookEvent.STOP, lambda p: order.append("b") or None, name="b")
    await reg.trigger(HookEvent.STOP, {})
    assert order == ["a", "b"]


async def test_disabled_hook_is_skipped():
    reg = HookRegistry()
    fired = []
    reg.register(HookEvent.STOP, lambda p: fired.append(1) or None, name="x")
    reg.disable("x")
    await reg.trigger(HookEvent.STOP, {})
    assert fired == []


async def test_sync_callback_wrapped():
    reg = HookRegistry()
    reg.register(HookEvent.STOP, lambda p: HookResult(), name="sync")
    result = await reg.trigger(HookEvent.STOP, {})
    assert isinstance(result, HookResult)


async def test_async_callback_supported():
    reg = HookRegistry()
    async def cb(p):
        return HookResult()
    reg.register(HookEvent.STOP, cb, name="async")
    result = await reg.trigger(HookEvent.STOP, {})
    assert isinstance(result, HookResult)


async def test_hook_exception_isolated():
    reg = HookRegistry()
    errors = []
    reg.error_sink = lambda msg: errors.append(msg)
    fired = []
    def bad(p): raise RuntimeError("boom")
    def good(p): fired.append(1); return None
    reg.register(HookEvent.STOP, bad, name="bad")
    reg.register(HookEvent.STOP, good, name="good")
    # Should not raise; should still call `good`.
    await reg.trigger(HookEvent.STOP, {})
    assert fired == [1]
    assert any("bad" in m and "RuntimeError" in m for m in errors)


# ---------- HookResult merging ----------

async def test_cancel_wins_over_passthrough():
    reg = HookRegistry()
    reg.register(HookEvent.PRE_TOOL_USE, lambda p: None, name="a")
    reg.register(HookEvent.PRE_TOOL_USE,
                 lambda p: HookResult(cancel=True, result="stop"),
                 name="b")
    reg.register(HookEvent.PRE_TOOL_USE, lambda p: None, name="c")
    result = await reg.trigger(HookEvent.PRE_TOOL_USE, {"tool": "t", "arguments": {}})
    assert result.cancel
    assert result.result == "stop"


async def test_modifications_stack():
    """Later hooks see modifications from earlier hooks via the payload dict."""
    reg = HookRegistry()
    def first(p):
        return HookResult(modified_user_input=p["user_input"].upper())
    def second(p):
        # Second hook should see the uppercased input via the payload.
        assert p["user_input"] == "HELLO"
        return HookResult(modified_user_input=p["user_input"] + "!")
    reg.register(HookEvent.USER_PROMPT_SUBMIT, first, name="upper")
    reg.register(HookEvent.USER_PROMPT_SUBMIT, second, name="bang")
    result = await reg.trigger(HookEvent.USER_PROMPT_SUBMIT, {"user_input": "hello"})
    assert result.modified_user_input == "HELLO!"


async def test_modified_post_result_propagates_via_payload():
    """A PostToolUse hook can rewrite `payload['result']` for later hooks."""
    reg = HookRegistry()
    def first(p):
        return HookResult(modified_post_result=p["result"] + "\n[reminder]")
    seen = []
    def second(p):
        seen.append(p["result"])
        return None
    reg.register(HookEvent.POST_TOOL_USE, first, name="inject")
    reg.register(HookEvent.POST_TOOL_USE, second, name="observe")
    result = await reg.trigger(HookEvent.POST_TOOL_USE,
                               {"tool": "x", "result": "original"})
    assert seen == ["original\n[reminder]"]
    assert result.modified_post_result == "original\n[reminder]"


# ---------- Agent integration ----------

def _agent(hooks: HookRegistry | None = None) -> Agent:
    return Agent(
        profile=Profile(name="t", api_key="sk", base_url="http://x/v1", model="m"),
        system_prompt="",
        hooks=hooks or HookRegistry(),
    )


async def test_user_prompt_submit_can_rewrite_input(monkeypatch):
    hooks = HookRegistry()
    hooks.register(
        HookEvent.USER_PROMPT_SUBMIT,
        lambda p: HookResult(modified_user_input="REWRITTEN"),
        name="rewrite",
    )
    agent = _agent(hooks)
    seen_user_msgs = []

    async def fake_stream_one_round(self):
        seen_user_msgs.extend(m.content for m in self.history if m.role == "user")
        yield AssistantTextDelta(text="ok")
        yield _RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)

    events = [ev async for ev in agent.run("original")]
    assert any("REWRITTEN" in m for m in seen_user_msgs)
    assert events[-1].reason == "stop"


async def test_user_prompt_submit_can_cancel(monkeypatch):
    hooks = HookRegistry()
    hooks.register(
        HookEvent.USER_PROMPT_SUBMIT,
        lambda p: HookResult(cancel=True, result="nope"),
        name="cancel",
    )
    agent = _agent(hooks)

    called = []
    async def fake_stream_one_round(self):
        called.append(1)
        yield AssistantTextDelta(text="should not happen")
        yield _RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)

    events = [ev async for ev in agent.run("hi")]
    assert called == [], "model should not have been called"
    completed = [e for e in events if isinstance(e, TurnCompleted)]
    assert completed and completed[-1].reason == "cancelled"


async def test_pre_tool_use_cancel_skips_dispatch(monkeypatch):
    hooks = HookRegistry()
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda p: HookResult(cancel=True, result="blocked"),
        name="blocker",
    )
    agent = _agent(hooks)
    dispatched = []

    async def fake_stream_one_round(self):
        if not getattr(self, "_called_once", False):
            self._called_once = True
            yield _RoundDone(tool_calls=[{
                "id": "c1", "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "ls"}'}
            }])
        else:
            yield AssistantTextDelta(text="done")
            yield _RoundDone(tool_calls=[])

    async def fake_dispatch(self, name, args):
        dispatched.append(name)
        return True, "actual output"

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)
    monkeypatch.setattr(ToolRegistry, "dispatch", fake_dispatch)

    events = [ev async for ev in agent.run("go")]
    # Dispatch never ran because the hook cancelled.
    assert dispatched == []
    # And the tool result that landed in history is the hook's `result`.
    tool_msgs = [m for m in agent.history if m.role == "tool"]
    assert tool_msgs and tool_msgs[0].content == "blocked"


async def test_pre_tool_use_can_rewrite_arguments(monkeypatch):
    hooks = HookRegistry()
    def rewrite(p):
        new_args = dict(p["arguments"])
        new_args["command"] = "echo rewritten"
        return HookResult(modified_arguments=new_args)
    hooks.register(HookEvent.PRE_TOOL_USE, rewrite, name="rewrite")
    agent = _agent(hooks)
    dispatched = []

    async def fake_stream_one_round(self):
        if not getattr(self, "_called_once", False):
            self._called_once = True
            yield _RoundDone(tool_calls=[{
                "id": "c1", "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "original"}'}
            }])
        else:
            yield AssistantTextDelta(text="done")
            yield _RoundDone(tool_calls=[])

    async def fake_dispatch(self, name, args):
        dispatched.append((name, args))
        return True, "ok"

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)
    monkeypatch.setattr(ToolRegistry, "dispatch", fake_dispatch)

    events = [ev async for ev in agent.run("go")]
    assert dispatched == [("bash", {"command": "echo rewritten"})]


async def test_post_tool_use_can_rewrite_history_result(monkeypatch):
    """A PostToolUse hook's modified_post_result updates the tool Message
    that gets appended to agent.history (so the model sees the rewrite next
    round)."""
    hooks = HookRegistry()
    def append_reminder(p):
        return HookResult(modified_post_result=p["result"] + "\n[reminder]")
    hooks.register(HookEvent.POST_TOOL_USE, append_reminder, name="injector")
    agent = _agent(hooks)

    async def fake_stream_one_round(self):
        if not getattr(self, "_called_once", False):
            self._called_once = True
            yield _RoundDone(tool_calls=[{
                "id": "c1", "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "ls"}'}
            }])
        else:
            yield AssistantTextDelta(text="done")
            yield _RoundDone(tool_calls=[])

    async def fake_dispatch(self, name, args):
        return True, "raw tool output"

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)
    monkeypatch.setattr(ToolRegistry, "dispatch", fake_dispatch)

    [ev async for ev in agent.run("go")]
    tool_msgs = [m for m in agent.history if m.role == "tool"]
    assert tool_msgs
    assert tool_msgs[0].content == "raw tool output\n[reminder]"


async def test_stop_fires_on_normal_completion(monkeypatch):
    fired = []
    hooks = HookRegistry()
    hooks.register(
        HookEvent.STOP,
        lambda p: fired.append(p) or None,
        name="stop",
    )
    agent = _agent(hooks)
    async def fake_stream_one_round(self):
        yield AssistantTextDelta(text="ok")
        yield _RoundDone(tool_calls=[])
    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)

    [ev async for ev in agent.run("hi")]
    assert fired and fired[0]["reason"] == "stop"


async def test_stop_fires_on_error(monkeypatch):
    fired = []
    hooks = HookRegistry()
    hooks.register(
        HookEvent.STOP,
        lambda p: fired.append(p) or None,
        name="stop",
    )
    agent = _agent(hooks)
    async def fake_stream_one_round(self):
        if False: yield
        raise RuntimeError("boom")
    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream_one_round)

    [ev async for ev in agent.run("hi")]
    assert fired and fired[0]["reason"] == "error"
    assert "boom" in (fired[0]["error"] or "")


# ---------- built-in hooks: audit log ----------

async def test_audit_log_writes_jsonl(tmp_path: Path):
    log_path = tmp_path / "calls.jsonl"
    pre = audit_log_hook("PreToolUse", log_path=log_path, session_id="sess1")
    post = audit_log_hook("PostToolUse", log_path=log_path, session_id="sess1")
    pre({"tool": "bash", "arguments": {"command": "ls"}, "call_id": "c1"})
    post({"tool": "bash", "arguments": {"command": "ls"}, "call_id": "c1",
          "ok": True, "result": "exit=0\nfoo bar"})
    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    a = json.loads(lines[0])
    b = json.loads(lines[1])
    assert a["event"] == "PreToolUse" and a["tool"] == "bash"
    assert a["session_id"] == "sess1"
    assert b["event"] == "PostToolUse" and b["ok"] is True
    assert b["session_id"] == "sess1"
    assert b["result"] == "exit=0\nfoo bar"


async def test_audit_log_stores_full_result_untruncated(tmp_path: Path):
    log_path = tmp_path / "calls.jsonl"
    post = audit_log_hook("PostToolUse", log_path=log_path)
    big = "x" * 5000
    post({"tool": "bash", "arguments": {}, "call_id": "c", "ok": True, "result": big})
    entry = json.loads(log_path.read_text().splitlines()[0])
    assert entry["result"] == big
    assert "result_preview" not in entry
    assert "result_truncated" not in entry
    assert "result_chars" not in entry


async def test_audit_log_omits_session_id_when_none(tmp_path: Path):
    log_path = tmp_path / "calls.jsonl"
    pre = audit_log_hook("PreToolUse", log_path=log_path)
    pre({"tool": "bash", "arguments": {}, "call_id": "c"})
    entry = json.loads(log_path.read_text().splitlines()[0])
    assert "session_id" not in entry


# ---------- built-in hooks: transcript ----------

def test_transcript_pre_calls_writer():
    captured = []
    hook = pre_tool_render_hook(lambda style, text: captured.append((style, text)))
    hook({"tool": "bash", "arguments": {"command": "ls"}, "call_id": "x"})
    assert captured == [("tool", "→ bash(command='ls')")]


def test_transcript_post_calls_writer():
    captured = []
    hook = post_tool_render_hook(lambda style, text: captured.append((style, text)))
    hook({"tool": "bash", "arguments": {}, "call_id": "x",
          "ok": True, "result": "exit=0"})
    assert captured == [("ok", "← bash [ok] exit=0")]
    hook({"tool": "bash", "arguments": {}, "call_id": "x",
          "ok": False, "result": "error: nope"})
    assert captured[-1] == ("err", "← bash [err] error: nope")


# ---------- built-in hooks: stop logger ----------

def test_stop_logger_writes_meta_line():
    captured = []
    hook = stop_logger_hook(lambda text: captured.append(text))
    hook({"reason": "stop", "error": None})
    hook({"reason": "error", "error": "oops"})
    assert captured == ["[turn finished: stop]",
                        "[turn finished: error — oops]"]


# ---------- built-in hooks: permission (round-trip) ----------

async def test_permission_hook_denies_built_in():
    eng = PermissionEngine(mode=Mode.SAFE)
    hook = permission_check_hook(engine=eng, approve=None)
    result = await hook({"tool": "bash",
                         "arguments": {"command": "rm -rf /"},
                         "call_id": "x"})
    assert result.cancel
    assert "blocked" in result.result


async def test_permission_hook_allows_built_in_allow():
    eng = PermissionEngine(mode=Mode.SAFE)
    hook = permission_check_hook(engine=eng, approve=None)
    result = await hook({"tool": "bash",
                         "arguments": {"command": "ls /tmp"},
                         "call_id": "x"})
    assert result is None  # allow path returns None (proceed)


async def test_permission_hook_yolo_skips_prompt():
    eng = PermissionEngine(mode=Mode.YOLO)
    approve_calls = []
    hook = permission_check_hook(
        engine=eng, approve=lambda ctx: approve_calls.append(ctx) or Verdict(allowed=True))
    result = await hook({"tool": "write_file",
                         "arguments": {"path": "/tmp/foo", "content": "x"},
                         "call_id": "x"})
    assert result is None
    assert approve_calls == []
