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

    def preview_prompts(
        self, *, first: int = 2, last: int = 1, max_chars: int = 200,
    ) -> list[str]:
        """Return a cheap preview: the first `first` + last `last` user prompts.

        Reads messages.jsonl line-by-line (not load_history, which parses the
        whole file into Message objects) and collects `content` where
        role == "user". Overlap is de-duplicated when the session has few user
        messages. Each prompt is collapsed to one line and truncated to
        `max_chars`. Missing file → []. Never raises.
        """
        if not self._messages_path.exists():
            return []
        users: list[str] = []
        try:
            with self._messages_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        # Truncated last line (crash mid-write) — skip.
                        continue
                    if data.get("role") == "user":
                        content = (data.get("content") or "").strip()
                        if content:
                            users.append(content)
        except OSError:
            return []

        if not users:
            return []
        # first N, plus the final one, without duplicating when they overlap.
        head = users[:first]
        picked = list(head)
        if last > 0:
            for tail in users[-last:]:
                if tail not in picked:
                    picked.append(tail)

        def _clip(s: str) -> str:
            s = " ".join(s.split())
            return s if len(s) <= max_chars else s[: max_chars - 1] + "…"

        return [_clip(s) for s in picked]

    # -- discovery --

    @classmethod
    def _scan_metas(cls, root: Path | None = None) -> list[SessionMeta]:
        """Parse every `<sid>/meta.json` under root, skipping unreadable ones.
        Sorted by last_at descending. A single bad sidecar never breaks the scan."""
        r = root if root is not None else _default_root()
        if not r.is_dir():
            return []
        metas: list[SessionMeta] = []
        for child in r.iterdir():
            mp = child / "meta.json"
            if not mp.is_file():
                continue
            try:
                metas.append(SessionMeta.load(mp))
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        metas.sort(key=lambda m: m.last_at, reverse=True)
        return metas

    @classmethod
    def list_for_workspace(
        cls, workspace: str, *, root: Path | None = None, limit: int = 50,
    ) -> list[SessionMeta]:
        metas = [m for m in cls._scan_metas(root) if m.workspace == workspace]
        return metas[:limit]

    @classmethod
    def list_all(
        cls, *, root: Path | None = None, limit: int = 200,
    ) -> list[SessionMeta]:
        """All sessions across every workspace, most-recent first."""
        return cls._scan_metas(root)[:limit]

    @classmethod
    def resumability(cls, meta: SessionMeta) -> str | None:
        """None if the session looks resumable, else a short reason string.

        Checks the workspace still exists and the meta's provider is still
        configured. Must never raise — a config-load failure is treated as
        'unknown', which does not block (returns None)."""
        try:
            if not Path(meta.workspace).is_dir():
                return "workspace gone"
        except OSError:
            return "workspace gone"
        try:
            from ..config import ConfigFile
            cfg = ConfigFile.load()
            if meta.provider not in cfg.providers:
                return "provider gone"
        except Exception:  # noqa: BLE001 — never block resume on a config read
            return None
        return None

