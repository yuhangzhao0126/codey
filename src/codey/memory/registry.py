"""In-memory registry: scan global + project tiers; project wins on collision."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .io import MEMORY_INDEX_FILENAME, parse_memory_md
from .models import Memory, Scope

# Inlined to avoid importing hooks.builtin.audit_log (which would
# trigger hooks.builtin/__init__.py -> tools -> memory and cycle).
_DEFAULT_AUDIT_LOG_PATH = Path.home() / ".cache" / "codey" / "calls.jsonl"

_INDEX_PREAMBLE = (
    "## Memory\n\n"
    "You have a long-term memory of facts and preferences learned across "
    "sessions. The names + descriptions below are always available; load "
    "a full entry with `load_memory(name=...)`. Use these to align with "
    "the user's preferences and project conventions.\n"
)


@dataclass
class MemoryRegistry:
    _memories: dict[str, Memory] = field(default_factory=dict)

    def get(self, name: str) -> Optional[Memory]:
        return self._memories.get(name)

    def names(self) -> list[str]:
        return sorted(self._memories.keys())

    def all(self) -> list[Memory]:
        return [self._memories[n] for n in self.names()]

    def list_meta(self) -> str:
        if not self._memories:
            return ""
        bullets = [
            f"- **{name}** — {self._memories[name].description}"
            for name in self.names()
        ]
        return _INDEX_PREAMBLE + "\n" + "\n".join(bullets)

    @classmethod
    def scan(
        cls,
        *,
        global_root: Path,
        project_root: Path,
        audit_log_path: Path | None = None,
    ) -> "MemoryRegistry":
        log = audit_log_path or _DEFAULT_AUDIT_LOG_PATH
        reg = cls()
        for scope, root in (("global", global_root), ("project", project_root)):
            reg._scan_one(scope, root, log)
        return reg

    def _scan_one(self, scope: Scope, root: Path, log: Path) -> None:
        if not root.is_dir():
            return
        for child in sorted(root.iterdir()):
            if not child.is_file():
                continue
            if child.suffix != ".md":
                continue
            if child.name == MEMORY_INDEX_FILENAME:
                continue
            try:
                text = child.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                _audit(log, {"event": "memory_invalid", "path": str(child),
                             "scope": scope, "reason": "read error"})
                continue
            parsed = parse_memory_md(
                text, filename_stem=child.stem, source_path=child, scope=scope,
            )
            if isinstance(parsed, str):
                _audit(log, {"event": "memory_invalid", "path": str(child),
                             "scope": scope, "reason": parsed})
                continue
            existing = self._memories.get(parsed.name)
            if existing is not None:
                _audit(log, {"event": "memory_override", "name": parsed.name,
                             "winner_scope": scope,
                             "winner_path": str(parsed.source_path),
                             "loser_scope": existing.scope,
                             "loser_path": str(existing.source_path)})
            self._memories[parsed.name] = parsed


def _audit(log_path: Path, entry: dict) -> None:
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), **entry}
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
