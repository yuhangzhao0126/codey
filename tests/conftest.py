"""Test fixtures: isolate codey config and permissions from the real user env."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from codey import config as config_mod
from codey import permissions as perms_mod


@pytest.fixture
def temp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Replace ~/.config/codey/config.toml + permissions.toml + project paths
    with temp files so tests never touch the real user state."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'default_provider = "alpha"\n'
        "\n"
        "[providers.alpha]\n"
        'base_url = "https://example.com/alpha/v1"\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
        "\n"
        "[providers.beta]\n"
        'base_url = "https://example.com/beta/v1"\n'
        'api_key  = "sk-beta"\n'
        'model    = "beta-model"\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)

    # Isolate the permission engine from the user's real permissions.toml
    # so a stray "yolo" mode from real usage can't poison test assertions.
    monkeypatch.setattr(perms_mod, "USER_PERMISSIONS_PATH", tmp_path / "permissions.toml")
    monkeypatch.setattr(perms_mod, "PROJECT_PERMISSIONS_PATH", tmp_path / "_project_permissions.toml")

    # Also clear env so nothing leaks in.
    for k in ("CODEY_API_KEY", "CODEY_BASE_URL", "CODEY_MODEL", "CODEY_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    yield cfg_path
