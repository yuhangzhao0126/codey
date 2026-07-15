"""Tests for the new Provider context-management fields."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.config import ConfigFile, DEFAULT_COMPACT_HEADROOM, DEFAULT_CONTEXT_WINDOW, DEFAULT_MAX_OUTPUT_TOKENS


def test_defaults_when_unset(temp_config: Path):
    cfg = ConfigFile.load()
    p = cfg.resolve("alpha")
    assert p.context_window == DEFAULT_CONTEXT_WINDOW
    assert p.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert p.compact_headroom == DEFAULT_COMPACT_HEADROOM


def test_defaults_are_documented_values():
    assert DEFAULT_CONTEXT_WINDOW == 1_000_000
    assert DEFAULT_MAX_OUTPUT_TOKENS == 4_096
    assert DEFAULT_COMPACT_HEADROOM == 13_000


def test_overrides_in_config(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'default_provider = "small"\n'
        "\n"
        "[providers.small]\n"
        'base_url = "https://example/v1"\n'
        'api_key  = "k"\n'
        'model    = "tiny"\n'
        "context_window    = 32000\n"
        "max_output_tokens = 1024\n"
        "compact_headroom  = 2000\n"
    )
    monkeypatch.setattr("codey.config.CONFIG_PATH", cfg_path)
    for k in ("CODEY_API_KEY", "CODEY_BASE_URL", "CODEY_MODEL", "CODEY_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    p = ConfigFile.load().resolve("small")
    assert p.context_window == 32000
    assert p.max_output_tokens == 1024
    assert p.compact_headroom == 2000


def test_partial_override(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'default_provider = "p"\n'
        "\n"
        "[providers.p]\n"
        'base_url = "https://x/v1"\n'
        'api_key  = "k"\n'
        'model    = "m"\n'
        "context_window = 16000\n"
    )
    monkeypatch.setattr("codey.config.CONFIG_PATH", cfg_path)
    for k in ("CODEY_API_KEY", "CODEY_BASE_URL", "CODEY_MODEL", "CODEY_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    p = ConfigFile.load().resolve("p")
    assert p.context_window == 16000
    assert p.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert p.compact_headroom == DEFAULT_COMPACT_HEADROOM
