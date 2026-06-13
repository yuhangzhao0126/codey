"""SessionMeta — small JSON sidecar describing a session.

Lives next to messages.jsonl under
~/.cache/codey/transcripts/<session_id>/meta.json. Forward-compatible:
unknown keys are preserved on load and round-tripped on save.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_KNOWN = {
    "session_id", "workspace", "profile",
    "started_at", "last_at", "title", "message_count",
}


@dataclass
class SessionMeta:
    session_id: str
    workspace: str
    profile: str
    started_at: str
    last_at: str
    title: str = ""
    message_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "session_id":    self.session_id,
            "workspace":     self.workspace,
            "profile":       self.profile,
            "started_at":    self.started_at,
            "last_at":       self.last_at,
            "title":         self.title,
            "message_count": self.message_count,
            **self.extra,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path) -> "SessionMeta":
        data = json.loads(path.read_text(encoding="utf-8"))
        extra = {k: v for k, v in data.items() if k not in _KNOWN}
        return cls(
            session_id=data["session_id"],
            workspace=data["workspace"],
            profile=data["profile"],
            started_at=data["started_at"],
            last_at=data["last_at"],
            title=data.get("title", ""),
            message_count=data.get("message_count", 0),
            extra=extra,
        )
