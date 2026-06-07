"""recent_reads: track successful read_file paths on a deque.

Reads from PostToolUse payloads. The agent's deque (created in
Session.build) is the same one llm_compact_history re-reads files from
at compaction time.

Dedupe-by-path: re-reading a path moves it to the end of the deque, so
the "5 most recent" set is the 5 most recently-touched distinct files.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Callable


def build_recent_reads_hook(dq: "deque[Path]") -> Callable[[dict[str, Any]], None]:
    def _hook(payload: dict[str, Any]):
        if payload.get("tool") != "read_file":
            return None
        if not payload.get("ok"):
            return None
        path_str = (payload.get("arguments") or {}).get("path")
        if not isinstance(path_str, str) or not path_str.strip():
            return None
        p = Path(path_str)
        try:
            dq.remove(p)
        except ValueError:
            pass
        dq.append(p)
        return None
    return _hook
