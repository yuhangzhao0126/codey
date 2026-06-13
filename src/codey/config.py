"""Profiles and config loading.

Each *profile* bundles (base_url, api_key, model) for one provider.
Profiles live in ~/.config/codey/config.toml. CODEY_* env vars (e.g. from .env)
only fill in *empty* profile fields — they don't override values you've set.

Resolution order for which profile is active (highest wins):
    1. explicit name passed to ConfigFile.resolve(name=...)
    2. $CODEY_PROFILE
    3. default_profile in config.toml

The system prompt is NOT part of a profile — see codey.prompt.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

CONFIG_PATH = Path.home() / ".config" / "codey" / "config.toml"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CONTEXT_WINDOW = 1_000_000      # 1M tokens — mainstream long-context tier
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
DEFAULT_COMPACT_HEADROOM = 13_000

DEFAULT_MEMORY_MAX_LOADED = 5


@dataclass(frozen=True)
class MemoryConfig:
    """User-level long-term-memory toggles, from the top-level [memory] block.

    One setting per user; no per-profile override in v1.
      - auto_extract: run the Stop-hook extractor after each turn.
      - side_query:   run the turn-start LLM side-query that pre-loads
                      relevant memory bodies. The always-on index in the
                      system prompt is unaffected by this toggle.
      - max_loaded:   cap on how many entries the side-query may pre-load.
    """
    auto_extract: bool = True
    side_query: bool = True
    max_loaded: int = DEFAULT_MEMORY_MAX_LOADED


@dataclass(frozen=True)
class Profile:
    name: str
    api_key: str
    base_url: str
    model: str
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    compact_headroom: int = DEFAULT_COMPACT_HEADROOM


@dataclass
class ConfigFile:
    """In-memory view of ~/.config/codey/config.toml."""

    default_profile: str
    profiles: dict[str, Profile]
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    @classmethod
    def load(cls) -> "ConfigFile":
        """Load from disk, bootstrapping from .env if the file doesn't exist."""
        load_dotenv()  # so env-var fallbacks/overrides work consistently

        if not CONFIG_PATH.exists():
            return cls._bootstrap_from_env()

        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)

        raw_profiles = data.get("profiles", {})
        if not raw_profiles:
            raise RuntimeError(
                f"{CONFIG_PATH} has no [profiles.*] entries. "
                "Add at least one profile."
            )

        profiles: dict[str, Profile] = {}
        for name, body in raw_profiles.items():
            profiles[name] = Profile(
                name=name,
                api_key=body.get("api_key", ""),
                base_url=body.get("base_url", DEFAULT_BASE_URL),
                model=body.get("model", DEFAULT_MODEL),
                context_window=int(body.get("context_window", DEFAULT_CONTEXT_WINDOW)),
                max_output_tokens=int(body.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)),
                compact_headroom=int(body.get("compact_headroom", DEFAULT_COMPACT_HEADROOM)),
            )

        default = data.get("default_profile") or next(iter(profiles))
        if default not in profiles:
            raise RuntimeError(
                f"default_profile = {default!r} not found in [profiles.*]"
            )
        return cls(default_profile=default, profiles=profiles,
                   memory=_parse_memory(data.get("memory", {})))

    @classmethod
    def _bootstrap_from_env(cls) -> "ConfigFile":
        """First-run: seed config.toml from CODEY_* env vars, then load it."""
        api_key = os.environ.get("CODEY_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"No config at {CONFIG_PATH} and CODEY_API_KEY is unset.\n"
                f"Either create {CONFIG_PATH} with a [profiles.*] block, "
                "or set CODEY_API_KEY in .env so codey can bootstrap one."
            )

        base_url = os.environ.get("CODEY_BASE_URL", DEFAULT_BASE_URL)
        model = os.environ.get("CODEY_MODEL", DEFAULT_MODEL)

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            'default_profile = "default"\n'
            "\n"
            "[profiles.default]\n"
            f'base_url = "{base_url}"\n'
            f'api_key  = "{api_key}"\n'
            f'model    = "{model}"\n'
        )
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
        print(f"(created {CONFIG_PATH} from .env — edit it to add more profiles)")
        return cls.load()

    def resolve(self, name: str | None = None) -> Profile:
        """Pick the active profile. Profile fields win; env vars fill empty fields."""
        chosen = name or os.environ.get("CODEY_PROFILE") or self.default_profile
        if chosen not in self.profiles:
            raise RuntimeError(
                f"Unknown profile {chosen!r}. "
                f"Available: {', '.join(sorted(self.profiles)) or '(none)'}"
            )
        profile = self.profiles[chosen]

        # env vars only fill in empty profile fields
        return replace(
            profile,
            api_key=profile.api_key or os.environ.get("CODEY_API_KEY", ""),
            base_url=profile.base_url or os.environ.get("CODEY_BASE_URL", DEFAULT_BASE_URL),
            model=profile.model or os.environ.get("CODEY_MODEL", DEFAULT_MODEL),
        )


def _parse_memory(raw: dict) -> MemoryConfig:
    """Parse the [memory] block, falling back to defaults on any bad value."""
    def _bool(key: str, default: bool) -> bool:
        val = raw.get(key, default)
        return bool(val) if isinstance(val, bool) else default

    try:
        max_loaded = int(raw.get("max_loaded", DEFAULT_MEMORY_MAX_LOADED))
        if max_loaded < 0:
            max_loaded = DEFAULT_MEMORY_MAX_LOADED
    except (TypeError, ValueError):
        max_loaded = DEFAULT_MEMORY_MAX_LOADED

    return MemoryConfig(
        auto_extract=_bool("auto_extract", True),
        side_query=_bool("side_query", True),
        max_loaded=max_loaded,
    )
