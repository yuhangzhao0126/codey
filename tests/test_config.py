"""Config parsing tests — focused on the [memory] block."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey import config as config_mod
from codey.config import ConfigFile, MemoryConfig, DEFAULT_MEMORY_MAX_LOADED


def _write_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_profile = "alpha"\n\n'
        "[profiles.alpha]\n"
        'base_url = "https://example.com/alpha/v1"\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
        + body
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)


def test_memory_defaults_when_block_absent(tmp_path, monkeypatch) -> None:
    _write_cfg(tmp_path, monkeypatch, "")
    cfg = ConfigFile.load()
    assert cfg.memory == MemoryConfig()
    assert cfg.memory.auto_extract is True
    assert cfg.memory.side_query is True
    assert cfg.memory.max_loaded == DEFAULT_MEMORY_MAX_LOADED


def test_memory_block_overrides(tmp_path, monkeypatch) -> None:
    _write_cfg(
        tmp_path, monkeypatch,
        "\n[memory]\nauto_extract = false\nside_query = false\nmax_loaded = 2\n",
    )
    cfg = ConfigFile.load()
    assert cfg.memory.auto_extract is False
    assert cfg.memory.side_query is False
    assert cfg.memory.max_loaded == 2


def test_memory_partial_block_keeps_other_defaults(tmp_path, monkeypatch) -> None:
    _write_cfg(tmp_path, monkeypatch, "\n[memory]\nmax_loaded = 10\n")
    cfg = ConfigFile.load()
    assert cfg.memory.auto_extract is True   # default preserved
    assert cfg.memory.side_query is True     # default preserved
    assert cfg.memory.max_loaded == 10


def test_memory_garbage_values_fall_back(tmp_path, monkeypatch) -> None:
    _write_cfg(
        tmp_path, monkeypatch,
        '\n[memory]\nauto_extract = "yes please"\nmax_loaded = -4\n',
    )
    cfg = ConfigFile.load()
    # Non-bool auto_extract → default True; negative max_loaded → default.
    assert cfg.memory.auto_extract is True
    assert cfg.memory.max_loaded == DEFAULT_MEMORY_MAX_LOADED


def test_configfile_constructed_directly_has_memory_default() -> None:
    # Tests that build ConfigFile(...) without the memory kwarg still work.
    cfg = ConfigFile(default_profile="x", profiles={})
    assert cfg.memory == MemoryConfig()
