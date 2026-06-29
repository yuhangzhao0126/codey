"""SessionStore — live append + load for one session's messages.jsonl.

Layout (one directory per session):

  <root>/<session_id>/
    meta.json          (SessionMeta)
    messages.jsonl     (one Message.to_wire() per line, appended live)

`root` defaults to ~/.cache/codey/transcripts/ — the same directory the
context package already writes tool_results/ and snapshots/ into. The
store does NOT lock the file: only the owning Agent writes a given
session, and reads happen at resume time when no writer is active.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.messages import Message
from .errors import SessionResumeError
from .meta import SessionMeta

_DEFAULT_ROOT = Path.home() / ".cache" / "codey" / "transcripts"


def _default_root() -> Path:
    """Indirection so tests can monkeypatch the module-level constant."""
    return _DEFAULT_ROOT


class SessionStore:
    def __init__(self, session_id: str, root: Path | None = None) -> None:
        self.session_id = session_id
        self._root = root if root is not None else _default_root()
        self._dir = self._root / session_id
        self._messages_path = self._dir / "messages.jsonl"
        self._meta_path = self._dir / "meta.json"

    # -- writes --

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            self._dir.chmod(0o700)
        except OSError:
            pass

    def save_meta(
        self,
        *,
        workspace: str,
        provider: str,
        started_at: str,
        title: str = "",
        message_count: int = 0,
    ) -> None:
        self._ensure_dir()
        meta = SessionMeta(
            session_id=self.session_id,
            workspace=workspace,
            provider=provider,
            started_at=started_at,
            last_at=started_at,
            title=title,
            message_count=message_count,
        )
        meta.save(self._meta_path)

    def touch_meta(
        self,
        *,
        last_at: str | None = None,
        title: str | None = None,
        message_count: int | None = None,
    ) -> None:
        if not self._meta_path.exists():
            return
        meta = SessionMeta.load(self._meta_path)
        if last_at is not None:
            meta.last_at = last_at
        if title is not None and not meta.title:
            meta.title = title
        if message_count is not None:
            meta.message_count = message_count
        meta.save(self._meta_path)

    def append_message(self, msg: Message) -> None:
        self._ensure_dir()
        line = json.dumps(msg.to_wire(), ensure_ascii=False)
        with self._messages_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        try:
            self._messages_path.chmod(0o600)
        except OSError:
            pass

    # -- reads --

    def load_history(self) -> list[Message]:
        if not self._messages_path.exists():
            return []
        out: list[Message] = []
        for line in self._messages_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # Truncated last line (crash mid-write) — skip silently.
                continue
            out.append(Message(
                role=data["role"],
                content=data.get("content", "") or "",
                tool_calls=data.get("tool_calls"),
                tool_call_id=data.get("tool_call_id"),
                name=data.get("name"),
            ))
        return out

    def load_meta(self) -> SessionMeta:
        if not self._meta_path.exists():
            raise SessionResumeError(f"session {self.session_id!r}: meta.json missing")
        return SessionMeta.load(self._meta_path)

    # -- discovery --

    @classmethod
    def list_for_workspace(
        cls, workspace: str, *, root: Path | None = None, limit: int = 50,
    ) -> list[SessionMeta]:
        r = root if root is not None else _default_root()
        if not r.is_dir():
            return []
        metas: list[SessionMeta] = []
        for child in r.iterdir():
            mp = child / "meta.json"
            if not mp.is_file():
                continue
            try:
                m = SessionMeta.load(mp)
            except (OSError, KeyError, json.JSONDecodeError):
                continue
            if m.workspace == workspace:
                metas.append(m)
        metas.sort(key=lambda m: m.last_at, reverse=True)
        return metas[:limit]
