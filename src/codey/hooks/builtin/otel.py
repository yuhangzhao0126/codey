"""OpenTelemetry tracing hook (opt-in).

Emits OTel spans for every model turn and tool call. Off by default; the
host opts in via the `--otel` CLI flag, the `CODEY_OTEL=1` env var, or a
`[otel] enabled = true` block in ~/.config/codey/config.toml.

Span shape per turn:

  turn (session_id, provider.name, provider.model)
  └─ tool_call: bash (tool, call_id, arguments)
  └─ tool_call: read_file …

Per-round spans are intentionally omitted: the agent loop in core/turn.py
doesn't surface a per-round event the hook can latch onto without leaking
OTel imports into core/. Tool calls are still grouped under their turn,
which gives every useful trace view (Phoenix, Jaeger, Tempo) what it
needs to walk a turn end-to-end.

Dependencies are imported lazily inside `build_otel_hooks()` so the base
codey install stays lean. If the host opts in without
`uv sync --extra observability`, the factory raises with a friendly fix-it
hint.
"""

from __future__ import annotations

import os
from typing import Any

from ..registry import HookCallback, HookResult


class OTelExtraMissing(RuntimeError):
    """Raised when OTel tracing is requested but the extra isn't installed."""

    def __init__(self) -> None:
        super().__init__(
            "OTel tracing was requested but the 'observability' extra isn't "
            "installed. Run: uv sync --extra observability"
        )


def otel_enabled(config_otel: dict | None = None) -> bool:
    """True if tracing is on. Checks env var first, then config block."""
    if os.environ.get("CODEY_OTEL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if config_otel and config_otel.get("enabled"):
        return True
    return False


def build_otel_hooks(
    *,
    session_id: str,
    provider_name: str,
    model: str,
    base_url: str,
    service_name: str | None = None,
    endpoint: str | None = None,
    tracer_provider: Any = None,
) -> dict[str, HookCallback]:
    """Set up the global tracer provider (idempotent) and return the four
    hook callbacks: `user_prompt_submit`, `pre_tool_use`, `post_tool_use`,
    `stop`. The caller registers each on the matching HookEvent.

    `tracer_provider` is an injection point for tests so they can pass an
    InMemorySpanExporter-backed provider instead of touching globals.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        raise OTelExtraMissing() from e

    if tracer_provider is None:
        # Set up a real exporter only if the caller didn't inject one.
        # Idempotent: re-setting the global provider would lose existing spans.
        if not isinstance(trace.get_tracer_provider(), TracerProvider):
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
            except ImportError as e:
                raise OTelExtraMissing() from e
            resource = Resource.create({
                "service.name": service_name
                                or os.environ.get("OTEL_SERVICE_NAME", "codey"),
            })
            provider = TracerProvider(resource=resource)
            exporter_kwargs = {}
            ep = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            if ep:
                # Standard OTLP HTTP traces path lives at /v1/traces.
                exporter_kwargs["endpoint"] = ep.rstrip("/") + "/v1/traces"
            # BatchSpanProcessor is async + drops if backed up — never blocks
            # the agent on a misconfigured collector.
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(**exporter_kwargs))
            )
            trace.set_tracer_provider(provider)
        tracer_provider = trace.get_tracer_provider()

    tracer = tracer_provider.get_tracer("codey", "0.0.1")

    # State carried across hook events: the active turn span and a map of
    # call_id → tool-call span so PostToolUse can finish the right one.
    state: dict[str, Any] = {"turn_span": None, "turn_ctx": None, "tool_spans": {}}

    def _start_turn(payload: dict[str, Any]) -> HookResult | None:
        span = tracer.start_span("turn", attributes={
            "codey.session_id": session_id,
            "codey.provider": provider_name,
            "codey.model": model,
            "codey.base_url": base_url,
        })
        state["turn_span"] = span
        # Stash the active context so child tool spans nest under the turn.
        from opentelemetry import trace as _trace
        state["turn_ctx"] = _trace.set_span_in_context(span)
        return None

    def _pre_tool(payload: dict[str, Any]) -> HookResult | None:
        if state["turn_ctx"] is None:
            return None
        call_id = payload.get("call_id") or "unknown"
        tool = payload.get("tool") or "unknown"
        span = tracer.start_span(
            f"tool_call:{tool}",
            context=state["turn_ctx"],
            attributes={
                "codey.tool": tool,
                "codey.call_id": call_id,
                "codey.arguments": str(payload.get("arguments")),
            },
        )
        state["tool_spans"][call_id] = span
        return None

    def _post_tool(payload: dict[str, Any]) -> HookResult | None:
        call_id = payload.get("call_id") or "unknown"
        span = state["tool_spans"].pop(call_id, None)
        if span is None:
            return None
        ok = bool(payload.get("ok"))
        result = payload.get("result") or ""
        span.set_attribute("codey.ok", ok)
        span.set_attribute("codey.result_chars", len(result))
        if not ok:
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(StatusCode.ERROR, "tool returned error"))
        span.end()
        return None

    def _stop(payload: dict[str, Any]) -> HookResult | None:
        span = state["turn_span"]
        if span is None:
            return None
        reason = payload.get("reason") or "unknown"
        span.set_attribute("codey.stop_reason", reason)
        if payload.get("error"):
            span.set_attribute("codey.error", payload["error"])
            from opentelemetry.trace import Status, StatusCode
            span.set_status(Status(StatusCode.ERROR, payload["error"]))
        span.end()
        # Reset so the next turn starts fresh.
        state["turn_span"] = None
        state["turn_ctx"] = None
        # Close any tool spans that never received PostToolUse (cancellation).
        for cid, sp in list(state["tool_spans"].items()):
            sp.end()
            del state["tool_spans"][cid]
        return None

    return {
        "user_prompt_submit": _start_turn,
        "pre_tool_use": _pre_tool,
        "post_tool_use": _post_tool,
        "stop": _stop,
    }
