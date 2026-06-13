"""Crash-safe JSONL queue for in-flight memory extractions."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def enqueue(path: Path, *, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    line_id = uuid.uuid4().hex[:12]
    record = {"_id": line_id, "_ts": _now(), **payload}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return line_id


def drain(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def ack(path: Path, *, line_id: str) -> None:
    if not path.is_file():
        return
    remaining: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if _id_of(s) == line_id:
            continue
        remaining.append(line)
    path.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")


def _id_of(line: str) -> str:
    try:
        return json.loads(line).get("_id", "")
    except json.JSONDecodeError:
        return ""
