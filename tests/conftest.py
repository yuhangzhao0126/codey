"""Test fixtures: pointing codey's config at a temp file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from codey import config as config_mod


@pytest.fixture
def temp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Replace ~/.config/codey/config.toml with a temp file holding two profiles."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'default_profile = "alpha"\n'
        "\n"
        "[profiles.alpha]\n"
        'base_url = "https://example.com/alpha/v1"\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
        "\n"
        "[profiles.beta]\n"
        'base_url = "https://example.com/beta/v1"\n'
        'api_key  = "sk-beta"\n'
        'model    = "beta-model"\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg_path)
    # Also clear env so nothing leaks in.
    for k in ("CODEY_API_KEY", "CODEY_BASE_URL", "CODEY_MODEL", "CODEY_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    yield cfg_path
