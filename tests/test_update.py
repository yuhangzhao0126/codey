"""Tests for `codey --update` self-update wiring."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import codey.app as app_mod


def test_run_update_errors_when_uv_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app_mod.shutil, "which", lambda _: None)
    rc = app_mod._run_update()
    assert rc == 1
    assert "uv is required" in capsys.readouterr().err


def test_run_update_invokes_uv_tool_install(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app_mod.shutil, "which", lambda _: "/usr/bin/uv")
    seen = {}

    def fake_run(cmd):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(app_mod.subprocess, "run", fake_run)
    rc = app_mod._run_update()

    assert rc == 0
    assert seen["cmd"][:4] == ["uv", "tool", "install", "--force"]
    assert seen["cmd"][4] == app_mod.CODEY_UPDATE_REF
    assert "update complete" in capsys.readouterr().out


def test_run_update_propagates_uv_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app_mod.shutil, "which", lambda _: "/usr/bin/uv")
    monkeypatch.setattr(
        app_mod.subprocess, "run", lambda cmd: SimpleNamespace(returncode=3)
    )
    rc = app_mod._run_update()
    assert rc == 3
    assert "update failed" in capsys.readouterr().err
