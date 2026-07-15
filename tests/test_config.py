"""Config parsing tests — focused on the [memory] block."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey import config as config_mod
from codey.config import (
    ConfigFile, MemoryConfig, DEFAULT_MEMORY_MAX_LOADED,
    PLACEHOLDER_API_KEY, set_provider_api_key,
)


def _write_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_provider = "alpha"\n\n'
        "[providers.alpha]\n"
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
    cfg = ConfigFile(default_provider="x", providers={})
    assert cfg.memory == MemoryConfig()


def test_needs_api_key_detects_placeholder_and_empty(tmp_path, monkeypatch) -> None:
    _write_cfg(tmp_path, monkeypatch, "")
    cfg = ConfigFile.load()
    real = cfg.providers["alpha"]
    assert real.needs_api_key is False
    from dataclasses import replace
    assert replace(real, api_key=PLACEHOLDER_API_KEY).needs_api_key is True
    assert replace(real, api_key="").needs_api_key is True


def test_set_provider_api_key_rewrites_only_target(tmp_path, monkeypatch) -> None:
    _write_cfg(tmp_path, monkeypatch,
               '\n[providers.beta]\napi_key = "sk-beta"\nmodel = "m"\n')
    set_provider_api_key("alpha", "sk-NEW")
    cfg = ConfigFile.load()
    assert cfg.providers["alpha"].api_key == "sk-NEW"
    assert cfg.providers["beta"].api_key == "sk-beta"
    assert cfg.providers["alpha"].model == "alpha-model"


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("CODEY_API_KEY", "CODEY_BASE_URL", "CODEY_MODEL", "CODEY_PROVIDER"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("bad", ["notaurl", "ftp://host/v1", "://nohost", "http:///v1"])
def test_resolve_rejects_malformed_base_url(tmp_path, monkeypatch, bad) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_provider = "alpha"\n\n'
        "[providers.alpha]\n"
        f'base_url = "{bad}"\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)
    _clear_env(monkeypatch)
    with pytest.raises(RuntimeError, match="invalid base_url"):
        ConfigFile.load().resolve()


def test_resolve_rejects_empty_base_url(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_provider = "alpha"\n\n'
        "[providers.alpha]\n"
        'base_url = ""\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)
    _clear_env(monkeypatch)
    # empty base_url falls back to DEFAULT_BASE_URL via env logic → still valid,
    # so force the env fallback empty too to hit the empty-url branch.
    monkeypatch.setenv("CODEY_BASE_URL", "")
    with pytest.raises(RuntimeError, match="base_url"):
        ConfigFile.load().resolve()


def test_resolve_accepts_valid_base_url(tmp_path, monkeypatch) -> None:
    _write_cfg(tmp_path, monkeypatch, "")
    _clear_env(monkeypatch)
    p = ConfigFile.load().resolve()
    assert p.base_url == "https://example.com/alpha/v1"


@pytest.mark.parametrize("endpoint,root", [
    ("https://api.x.com/v1/chat/completions", "https://api.x.com/v1"),
    ("https://api.x.com/v1/completions", "https://api.x.com/v1"),
    ("https://api.x.com/v1/embeddings", "https://api.x.com/v1"),
    ("https://api.x.com/v1/models", "https://api.x.com/v1"),
    ("https://api.x.com/openai/v1/chat/completions", "https://api.x.com/openai/v1"),
    ("https://api.x.com/chat/completions/", "https://api.x.com/"),
])
def test_resolve_rejects_full_endpoint_and_suggests_root(
    tmp_path, monkeypatch, endpoint, root
) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_provider = "alpha"\n\n'
        "[providers.alpha]\n"
        f'base_url = "{endpoint}"\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)
    _clear_env(monkeypatch)
    with pytest.raises(RuntimeError, match="full endpoint") as exc:
        ConfigFile.load().resolve()
    assert f'base_url = "{root}"' in str(exc.value)


def test_resolve_accepts_api_root_with_v1(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_provider = "alpha"\n\n'
        "[providers.alpha]\n"
        'base_url = "https://api.deepseek.com/v1"\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)
    _clear_env(monkeypatch)
    assert ConfigFile.load().resolve().base_url == "https://api.deepseek.com/v1"

