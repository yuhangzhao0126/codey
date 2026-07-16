"""Tests for the two-level resume feature: store discovery/preview/resumability
and the resume picker's pure render/format helpers."""
from __future__ import annotations

import json
from pathlib import Path

from codey.core.messages import Message
from codey.session_store import SessionMeta, SessionStore
from codey.ui.modals.resume_picker import render_row, format_detail


def _mk(root: Path, sid: str, ws: str, *, provider: str = "alpha",
        last_at: str = "2026-06-13T10:00:00", users: list[str] | None = None) -> SessionStore:
    store = SessionStore(session_id=sid, root=root)
    store.save_meta(workspace=ws, provider=provider, started_at=last_at)
    store.touch_meta(last_at=last_at)
    if users is not None:
        store.append_message(Message(role="system", content="sys"))
        for i, u in enumerate(users):
            store.append_message(Message(role="user", content=u))
            store.append_message(Message(role="assistant", content=f"a{i}"))
    return store


# ---- list_all / list_for_workspace ----

def test_list_all_spans_workspaces_sorted(tmp_path: Path) -> None:
    _mk(tmp_path, "s1", "/ws/one", last_at="2026-06-01T00:00:00")
    _mk(tmp_path, "s2", "/ws/two", last_at="2026-06-03T00:00:00")
    _mk(tmp_path, "s3", "/ws/one", last_at="2026-06-02T00:00:00")

    allm = SessionStore.list_all(root=tmp_path)
    assert [m.session_id for m in allm] == ["s2", "s3", "s1"]  # last_at desc

    ws_one = SessionStore.list_for_workspace("/ws/one", root=tmp_path)
    assert {m.session_id for m in ws_one} == {"s1", "s3"}


def test_list_all_ignores_bad_sidecar(tmp_path: Path) -> None:
    _mk(tmp_path, "good", "/ws")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "meta.json").write_text("{ not json", encoding="utf-8")

    allm = SessionStore.list_all(root=tmp_path)
    assert [m.session_id for m in allm] == ["good"]


def test_list_all_respects_limit(tmp_path: Path) -> None:
    for i in range(5):
        _mk(tmp_path, f"s{i}", "/ws", last_at=f"2026-06-0{i+1}T00:00:00")
    assert len(SessionStore.list_all(root=tmp_path, limit=2)) == 2


# ---- preview_prompts ----

def test_preview_first_two_plus_last(tmp_path: Path) -> None:
    store = _mk(tmp_path, "p", "/ws",
                users=["first", "second", "third", "fourth"])
    assert store.preview_prompts() == ["first", "second", "fourth"]


def test_preview_dedups_when_few(tmp_path: Path) -> None:
    store = _mk(tmp_path, "p", "/ws", users=["only"])
    assert store.preview_prompts() == ["only"]

    store2 = _mk(tmp_path, "p2", "/ws", users=["a", "b"])
    # first 2 = [a, b], last 1 = b (already present) → no dup
    assert store2.preview_prompts() == ["a", "b"]


def test_preview_truncates(tmp_path: Path) -> None:
    long = "x" * 500
    store = _mk(tmp_path, "p", "/ws", users=[long])
    out = store.preview_prompts(max_chars=50)
    assert len(out[0]) == 50
    assert out[0].endswith("…")


def test_preview_collapses_whitespace(tmp_path: Path) -> None:
    store = _mk(tmp_path, "p", "/ws", users=["line one\n\nline   two"])
    assert store.preview_prompts() == ["line one line two"]


def test_preview_missing_file_returns_empty(tmp_path: Path) -> None:
    store = _mk(tmp_path, "p", "/ws")  # no messages appended
    assert store.preview_prompts() == []


def test_preview_tolerates_truncated_last_line(tmp_path: Path) -> None:
    store = _mk(tmp_path, "p", "/ws", users=["good one"])
    # append a torn final line
    with store._messages_path.open("a", encoding="utf-8") as f:
        f.write('{"role": "user", "content": "trunca')
    assert store.preview_prompts() == ["good one"]


# ---- resumability ----

def test_resumability_flags_workspace_gone(tmp_path: Path) -> None:
    m = SessionMeta(session_id="s", workspace=str(tmp_path / "nope"),
                    provider="alpha", started_at="t", last_at="t")
    assert SessionStore.resumability(m) == "workspace gone"


def test_resumability_flags_provider_gone(tmp_path: Path, temp_config) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    m = SessionMeta(session_id="s", workspace=str(ws),
                    provider="ghost", started_at="t", last_at="t")
    assert SessionStore.resumability(m) == "provider gone"


def test_resumability_healthy_returns_none(tmp_path: Path, temp_config) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    m = SessionMeta(session_id="s", workspace=str(ws),
                    provider="alpha", started_at="t", last_at="t")
    assert SessionStore.resumability(m) is None


# ---- picker render/format helpers (pure) ----

def _meta(sid: str, ws: str = "/repos/foo") -> SessionMeta:
    return SessionMeta(session_id=sid, workspace=ws, provider="alpha",
                       started_at="2026-06-13T10:00:00",
                       last_at="2026-06-13T10:00:00", message_count=12)


def test_render_row_healthy_vs_unavailable() -> None:
    m = _meta("abc12345")
    healthy = render_row(m, None)
    assert "abc12345" in healthy
    assert "unavailable" not in healthy

    marked = render_row(m, "workspace gone")
    assert "[unavailable: workspace gone]" in marked
    assert "[dim]" in marked  # dimmed markup


def test_format_detail_shows_id_dir_and_prompts() -> None:
    m = _meta("abc12345", ws="/repos/foo")
    detail = format_detail(m, ["first prompt", "second", "last prompt"], None)
    assert "abc12345" in detail
    assert "/repos/foo" in detail
    assert "first prompt" in detail
    assert "last prompt" in detail
    assert "⋯" in detail  # separator between first-block and last


def test_format_detail_marks_unavailable() -> None:
    m = _meta("abc12345")
    detail = format_detail(m, [], "provider gone")
    assert "cannot resume: provider gone" in detail


def test_format_detail_handles_no_prompts() -> None:
    m = _meta("abc12345")
    detail = format_detail(m, [], None)
    assert "no user prompts recorded" in detail
