"""Tests for codey.session_store."""
from __future__ import annotations

import json
from pathlib import Path

from codey.core.messages import Message
from codey.session_store import SessionMeta, SessionStore


def test_session_meta_roundtrip(tmp_path: Path) -> None:
    meta = SessionMeta(
        session_id="abc12345",
        workspace=str(tmp_path),
        provider="alpha",
        started_at="2026-06-13T14:00:00",
        last_at="2026-06-13T14:02:00",
        title="hello",
        message_count=3,
    )
    path = tmp_path / "meta.json"
    meta.save(path)
    loaded = SessionMeta.load(path)
    assert loaded == meta


def test_session_meta_load_preserves_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"
    path.write_text(
        '{"session_id":"x","workspace":"/w","provider":"p","started_at":"t",'
        '"last_at":"t","title":"","message_count":0,"future_key":"keep me"}'
    )
    loaded = SessionMeta.load(path)
    assert loaded.extra == {"future_key": "keep me"}
    loaded.save(path)
    data = json.loads(path.read_text())
    assert data["future_key"] == "keep me"


def test_session_store_append_and_load(tmp_path: Path) -> None:
    store = SessionStore(session_id="aaa11111", root=tmp_path)
    store.save_meta(workspace=str(tmp_path), provider="alpha",
                    started_at="2026-06-13T14:00:00")
    store.append_message(Message(role="system", content="sys"))
    store.append_message(Message(role="user", content="hello"))
    store.append_message(Message(role="assistant", content="hi"))

    history = store.load_history()
    assert [m.role for m in history] == ["system", "user", "assistant"]
    assert history[1].content == "hello"


def test_session_store_load_history_truncated_last_line(tmp_path: Path) -> None:
    store = SessionStore(session_id="bbb22222", root=tmp_path)
    store.save_meta(workspace=str(tmp_path), provider="p", started_at="t")
    store.append_message(Message(role="user", content="ok"))
    with store._messages_path.open("a", encoding="utf-8") as f:
        f.write('{"role":"assistant","content":"truncat')
    history = store.load_history()
    assert [m.role for m in history] == ["user"]


def test_list_for_workspace_filters_and_sorts(tmp_path: Path) -> None:
    for sid, ws, last in [
        ("a11", str(tmp_path / "A"), "2026-06-13T10:00:00"),
        ("a22", str(tmp_path / "A"), "2026-06-13T12:00:00"),
        ("b33", str(tmp_path / "B"), "2026-06-13T11:00:00"),
    ]:
        s = SessionStore(session_id=sid, root=tmp_path)
        s.save_meta(workspace=ws, provider="p", started_at=last)
        s.touch_meta(last_at=last, message_count=1)

    listed = SessionStore.list_for_workspace(str(tmp_path / "A"), root=tmp_path)
    assert [m.session_id for m in listed] == ["a22", "a11"]
    assert all(m.workspace == str(tmp_path / "A") for m in listed)
