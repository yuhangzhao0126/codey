"""OTel hook: when enabled, every turn produces a `turn` span with nested
`tool_call:*` children. Skipped when the `observability` extra isn't
installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from codey.hooks import HookEvent, HookRegistry
from codey.hooks.builtin.otel import build_otel_hooks


def _fresh_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


async def test_one_turn_emits_turn_span_with_tool_call_children():
    provider, exporter = _fresh_provider()
    cbs = build_otel_hooks(
        session_id="sess1",
        profile_name="alpha",
        model="alpha-model",
        base_url="http://x/v1",
        tracer_provider=provider,
    )
    reg = HookRegistry()
    reg.register(HookEvent.USER_PROMPT_SUBMIT, cbs["user_prompt_submit"], name="otel_turn_start")
    reg.register(HookEvent.PRE_TOOL_USE,       cbs["pre_tool_use"],       name="otel_tool_pre")
    reg.register(HookEvent.POST_TOOL_USE,      cbs["post_tool_use"],      name="otel_tool_post")
    reg.register(HookEvent.STOP,               cbs["stop"],               name="otel_turn_stop")

    await reg.trigger(HookEvent.USER_PROMPT_SUBMIT, {"user_input": "hi"})
    await reg.trigger(HookEvent.PRE_TOOL_USE,  {"tool": "bash", "call_id": "c1", "arguments": {"command": "ls"}})
    await reg.trigger(HookEvent.POST_TOOL_USE, {"tool": "bash", "call_id": "c1", "ok": True, "result": "files"})
    await reg.trigger(HookEvent.PRE_TOOL_USE,  {"tool": "read_file", "call_id": "c2", "arguments": {"path": "x"}})
    await reg.trigger(HookEvent.POST_TOOL_USE, {"tool": "read_file", "call_id": "c2", "ok": True, "result": "..."})
    await reg.trigger(HookEvent.STOP, {"reason": "stop", "error": None})

    spans = exporter.get_finished_spans()
    names = sorted(s.name for s in spans)
    assert names == ["tool_call:bash", "tool_call:read_file", "turn"]

    turn = next(s for s in spans if s.name == "turn")
    assert turn.attributes["codey.session_id"] == "sess1"
    assert turn.attributes["codey.profile"] == "alpha"
    assert turn.attributes["codey.model"] == "alpha-model"
    assert turn.attributes["codey.stop_reason"] == "stop"

    bash_span = next(s for s in spans if s.name == "tool_call:bash")
    assert bash_span.attributes["codey.tool"] == "bash"
    assert bash_span.attributes["codey.call_id"] == "c1"
    assert bash_span.attributes["codey.ok"] is True
    # Tool spans must be children of the turn span (same trace_id, parent set).
    assert bash_span.context.trace_id == turn.context.trace_id
    assert bash_span.parent is not None
    assert bash_span.parent.span_id == turn.context.span_id


async def test_stop_with_error_marks_turn_span_failed():
    provider, exporter = _fresh_provider()
    cbs = build_otel_hooks(
        session_id="s", profile_name="p", model="m", base_url="u",
        tracer_provider=provider,
    )
    reg = HookRegistry()
    reg.register(HookEvent.USER_PROMPT_SUBMIT, cbs["user_prompt_submit"], name="otel_turn_start")
    reg.register(HookEvent.STOP, cbs["stop"], name="otel_turn_stop")

    await reg.trigger(HookEvent.USER_PROMPT_SUBMIT, {"user_input": "hi"})
    await reg.trigger(HookEvent.STOP, {"reason": "error", "error": "boom"})

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    turn = spans[0]
    assert turn.attributes["codey.error"] == "boom"
    assert turn.status.status_code == trace.StatusCode.ERROR


def test_otel_enabled_reads_env_var(monkeypatch):
    from codey.hooks.builtin.otel import otel_enabled
    monkeypatch.delenv("CODEY_OTEL", raising=False)
    assert otel_enabled() is False
    monkeypatch.setenv("CODEY_OTEL", "1")
    assert otel_enabled() is True
    monkeypatch.setenv("CODEY_OTEL", "no")
    assert otel_enabled() is False


def test_otel_enabled_reads_config_block(monkeypatch):
    from codey.hooks.builtin.otel import otel_enabled
    monkeypatch.delenv("CODEY_OTEL", raising=False)
    assert otel_enabled({"enabled": True}) is True
    assert otel_enabled({"enabled": False}) is False
    assert otel_enabled({}) is False
