"""Audit log hook: append-only JSONL trail of every tool call + result.

One line per Pre/PostToolUse event. Default location is
~/.cache/codey/calls.jsonl; pass a different path for tests.

Failures (disk full, permission denied) are swallowed — auditing should
never break the agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..hooks import HookCallback, HookResult

DEFAULT_LOG_PATH = Path.home() / ".cache" / "codey" / "calls.jsonl"
RESULT_PREVIEW_CHARS = 500


def audit_log_hook(
    event_kind: str,
    log_path: Path | None = None,
    now: "callable | None" = None,
) -> HookCallback:
    """Return a hook callback. `event_kind` is "PreToolUse" or "PostToolUse"
    and is recorded in each line so a single tail of the file shows both.

    `now` is an optional clock injection point for tests. If None, we use
    datetime.now (called at fire time, not import time)."""
    path = log_path or DEFAULT_LOG_PATH

    def _ts() -> str:
        # Imported lazily so tests can monkeypatch easily and so the import
        # doesn't pin the clock at module load.
        from datetime import datetime
        return (now or datetime.now)().isoformat(timespec="seconds")

    def hook(payload: dict[str, Any]) -> HookResult | None:
        try:
            line = _line(event_kind, _ts(), payload)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Logging must never break the agent.
            pass
        return None
    return hook


def _line(event_kind: str, ts: str, payload: dict[str, Any]) -> str:
    entry: dict[str, Any] = {
        "ts": ts,
        "event": event_kind,
        "tool": payload.get("tool"),
        "arguments": payload.get("arguments"),
        "call_id": payload.get("call_id"),
    }
    if event_kind == "PostToolUse":
        entry["ok"] = payload.get("ok")
        result = payload.get("result") or ""
        if len(result) > RESULT_PREVIEW_CHARS:
            entry["result_preview"] = result[:RESULT_PREVIEW_CHARS] + "…"
            entry["result_truncated"] = True
            entry["result_chars"] = len(result)
        else:
            entry["result_preview"] = result
    return json.dumps(entry, default=str, ensure_ascii=False)
