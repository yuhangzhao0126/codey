"""Audit log hook: append-only JSONL trail of every tool call + result.

One line per Pre/PostToolUse event. Default location is
~/.cache/codey/calls.jsonl; pass a different path for tests.

Failures (disk full, permission denied) are swallowed — auditing should
never break the agent.

Each line includes the host-supplied `session_id` so a single tail of the
file can be filtered to one run with `jq 'select(.session_id == "abc123")'`.
The full tool `result` is stored — for a personal-use agent the disk cost
is negligible and being able to grep the raw output is exactly what makes
running the TUI without per-call transcript lines tolerable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..registry import HookCallback, HookResult

DEFAULT_LOG_PATH = Path.home() / ".cache" / "codey" / "calls.jsonl"


def audit_log_hook(
    event_kind: str,
    log_path: Path | None = None,
    now: "callable | None" = None,
    session_id: str | None = None,
) -> HookCallback:
    """Return a hook callback. `event_kind` is "PreToolUse" or "PostToolUse"
    and is recorded in each line so a single tail of the file shows both.

    `session_id` is stamped onto every line; omit it (None) and the field
    is left out.

    `now` is an optional clock injection point for tests. If None, we use
    datetime.now (called at fire time, not import time)."""
    path = log_path or DEFAULT_LOG_PATH

    def _ts() -> str:
        from datetime import datetime
        return (now or datetime.now)().isoformat(timespec="seconds")

    def hook(payload: dict[str, Any]) -> HookResult | None:
        try:
            line = _line(event_kind, _ts(), payload, session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        return None
    return hook


def _line(event_kind: str, ts: str, payload: dict[str, Any],
          session_id: str | None) -> str:
    entry: dict[str, Any] = {"ts": ts}
    if session_id is not None:
        entry["session_id"] = session_id
    entry["event"] = event_kind
    entry["tool"] = payload.get("tool")
    entry["arguments"] = payload.get("arguments")
    entry["call_id"] = payload.get("call_id")
    if event_kind == "PostToolUse":
        entry["ok"] = payload.get("ok")
        entry["result"] = payload.get("result") or ""
    return json.dumps(entry, default=str, ensure_ascii=False)
