"""Tests for the profile→provider session-meta migration."""
from __future__ import annotations

import json
from pathlib import Path

from codey.session_store import SessionMeta
from codey.session_store.migrate import migrate_all, migrate_meta_file


def _write_meta(root: Path, sid: str, body: dict) -> Path:
    d = root / sid
    d.mkdir(parents=True, exist_ok=True)
    p = d / "meta.json"
    p.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return p


def _old(sid: str) -> dict:
    return {
        "session_id": sid,
        "workspace": "/tmp/ws",
        "profile": "alpha",
        "started_at": "2026-06-13T21:48:51",
        "last_at": "2026-06-13T21:48:51",
        "title": "",
        "message_count": 0,
    }


def test_migrate_renames_profile_to_provider(tmp_path: Path) -> None:
    p = _write_meta(tmp_path, "00276680", _old("00276680"))
    assert migrate_meta_file(p) == "migrated"

    data = json.loads(p.read_text())
    assert "profile" not in data
    assert data["provider"] == "alpha"
    # everything else preserved
    assert data["session_id"] == "00276680"
    assert data["started_at"] == "2026-06-13T21:48:51"

    # and the sidecar is now loadable without KeyError
    meta = SessionMeta.load(p)
    assert meta.provider == "alpha"


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    p = _write_meta(tmp_path, "s1", _old("s1"))
    assert migrate_meta_file(p) == "migrated"
    assert migrate_meta_file(p) == "skipped"


def test_migrate_skips_new_schema(tmp_path: Path) -> None:
    body = _old("s2")
    del body["profile"]
    body["provider"] = "beta"
    p = _write_meta(tmp_path, "s2", body)
    assert migrate_meta_file(p) == "skipped"
    assert json.loads(p.read_text())["provider"] == "beta"


def test_migrate_prefers_existing_provider_over_profile(tmp_path: Path) -> None:
    # A sidecar with BOTH keys should keep provider and not clobber it.
    body = _old("s3")
    body["provider"] = "already-correct"
    p = _write_meta(tmp_path, "s3", body)
    assert migrate_meta_file(p) == "skipped"
    data = json.loads(p.read_text())
    assert data["provider"] == "already-correct"
    assert data["profile"] == "alpha"  # left as-is; provider wins


def test_migrate_all_walks_root_and_reports(tmp_path: Path) -> None:
    _write_meta(tmp_path, "old1", _old("old1"))
    _write_meta(tmp_path, "old2", _old("old2"))
    new_body = _old("new1")
    del new_body["profile"]
    new_body["provider"] = "gamma"
    _write_meta(tmp_path, "new1", new_body)
    # a dir with no meta.json is ignored
    (tmp_path / "empty").mkdir()

    result = migrate_all(root=tmp_path)
    assert sorted(result.migrated) == ["old1", "old2"]
    assert result.skipped == ["new1"]
    assert result.failed == []


def test_migrate_all_records_malformed_as_failed(tmp_path: Path) -> None:
    d = tmp_path / "bad"
    d.mkdir()
    (d / "meta.json").write_text("{ not json", encoding="utf-8")

    result = migrate_all(root=tmp_path)
    assert result.migrated == []
    assert [sid for sid, _ in result.failed] == ["bad"]


def test_migrate_all_missing_root_is_noop(tmp_path: Path) -> None:
    result = migrate_all(root=tmp_path / "does-not-exist")
    assert result == type(result)(migrated=[], skipped=[], failed=[])
