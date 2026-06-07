"""Disk I/O for context-management spill files.

Two artifact types:

  - Persisted tool results: raw text bodies, one per file, under
    ~/.cache/codey/transcripts/<session_id>/tool_results/<call_id>-<tool>.txt
    These are written by tool_result_budget when a round's tool messages
    exceed the budget. The original message in history is rewritten to a
    short `<persisted output>` stub pointing at this path.

  - History snapshots: JSON arrays of Message.to_wire() dicts, written
    before llm_compact_history or reactive_compact mutate history. Useful
    for debugging "what was in the prompt right before we summarized."

All writes are best-effort: callers handle exceptions and emit meta lines.
Files are written with mode 0o600 on systems that honor it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal

from ..core.messages import Message

_CACHE_ROOT = Path.home() / ".cache" / "codey"

SnapshotKind = Literal["proactive", "reactive"]

_SAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(s: str) -> str:
    """Replace anything not alnum/dot/underscore/dash with '_'. Empty → 'x'."""
    cleaned = _SAFE_CHAR_RE.sub("_", s) if s else ""
    return cleaned or "x"


def persisted_root_for(session_id: str) -> Path:
    return _CACHE_ROOT / "transcripts" / _safe(session_id) / "tool_results"


def snapshots_root_for(session_id: str) -> Path:
    return _CACHE_ROOT / "transcripts" / _safe(session_id) / "snapshots"


def _chmod_quiet(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def write_persisted_tool_result(
    *, session_id: str, call_id: str, tool_name: str, body: str,
) -> Path:
    """Write a tool result body to disk and return the file path."""
    root = persisted_root_for(session_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_safe(call_id)}-{_safe(tool_name)}.txt"
    path.write_text(body, encoding="utf-8")
    _chmod_quiet(path, 0o600)
    return path


def write_history_snapshot(
    *, session_id: str, history: Iterable[Message], kind: SnapshotKind,
) -> Path:
    """Snapshot `history` as JSON and return the path."""
    root = snapshots_root_for(session_id)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds").replace(":", "-")
    path = root / f"{stamp}-{kind}.json"
    payload = [m.to_wire() for m in history]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _chmod_quiet(path, 0o600)
    return path
