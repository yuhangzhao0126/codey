"""MemoryStore — the single chokepoint for memory mutations on disk.

Every method rebuilds the affected tier's MEMORY.md and emits an audit
line to ~/.cache/codey/calls.jsonl (or the path supplied).

An asyncio.Lock serializes writes within one process. Cross-process
contention is rare; we accept last-write-wins.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from .errors import MemoryWriteError
from .io import delete_memory, rebuild_index, write_memory
from .models import Memory, Scope

# Same default path the audit_log hook uses (inlined to avoid importing
# hooks.builtin.audit_log, which would cycle via hooks.builtin -> tools -> memory).
_DEFAULT_AUDIT_LOG_PATH = Path.home() / ".cache" / "codey" / "calls.jsonl"

Source = Literal["extract", "tool", "slash"]


@dataclass
class MemoryStore:
    global_root: Path
    project_root: Path
    audit_log_path: Path = field(default_factory=lambda: _DEFAULT_AUDIT_LOG_PATH)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def _root_for(self, scope: Scope) -> Path:
        return self.global_root if scope == "global" else self.project_root

    async def write(self, memory: Memory, *, scope: Scope, source: Source) -> Path:
        async with self._lock:
            root = self._root_for(scope)
            target = Memory(
                name=memory.name, description=memory.description,
                type=memory.type, body=memory.body,
                created_at=memory.created_at, updated_at=memory.updated_at,
                source_session=memory.source_session, scope=scope,
                source_path=root / f"{memory.name}.md",
            )
            try:
                path = write_memory(target, tier_root=root)
            except MemoryWriteError as e:
                self._audit({"event": "memory_write_failed", "name": memory.name,
                             "scope": scope, "error": str(e), "source": source})
                raise
            rebuild_index(tier_root=root)
            self._audit({"event": "memory_write", "name": memory.name,
                         "scope": scope, "source": source})
            return path

    async def delete(self, name: str, *, scope: Scope, source: Source) -> bool:
        async with self._lock:
            root = self._root_for(scope)
            removed = delete_memory(name, tier_root=root)
            rebuild_index(tier_root=root)
            self._audit({"event": "memory_delete", "name": name, "scope": scope,
                         "removed": removed, "source": source})
            return removed

    async def rebuild_index(self, scope: Scope) -> Path:
        async with self._lock:
            return rebuild_index(tier_root=self._root_for(scope))

    def _audit(self, entry: dict) -> None:
        try:
            entry = {"ts": datetime.now().isoformat(timespec="seconds"), **entry}
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
