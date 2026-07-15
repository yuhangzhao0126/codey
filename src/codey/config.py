"""Providers and config loading.

Each *provider* bundles (base_url, api_key, model) for one provider.
Providers live in ~/.config/codey/config.toml. CODEY_* env vars (e.g. from .env)
only fill in *empty* provider fields — they don't override values you've set.

Resolution order for which provider is active (highest wins):
    1. explicit name passed to ConfigFile.resolve(name=...)
    2. $CODEY_PROVIDER
    3. default_provider in config.toml

The system prompt is NOT part of a provider — see codey.prompt.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

CONFIG_PATH = Path.home() / ".config" / "codey" / "config.toml"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CONTEXT_WINDOW = 1_000_000      # 1M tokens — mainstream long-context tier
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
DEFAULT_COMPACT_HEADROOM = 13_000

DEFAULT_MEMORY_MAX_LOADED = 5

PLACEHOLDER_API_KEY = "sk-..."  # what install.sh/.ps1 seed; means "not yet set"


def _validate_base_url(provider_name: str, base_url: str) -> str:
    """Validate base_url is a well-formed OpenAI-compatible API *root* URL.

    The OpenAI SDK appends the endpoint path (e.g. `/chat/completions`) to
    base_url itself, so base_url must be the API root — not a full endpoint.
    A common paste mistake is copying the whole `.../v1/chat/completions` URL;
    reject it here with a clear message instead of letting requests 404 later.
    """
    if not base_url or not base_url.strip():
        raise RuntimeError(
            f"Provider {provider_name!r} has an empty base_url. "
            f"Set base_url in [providers.{provider_name}] at {CONFIG_PATH} "
            "(e.g. base_url = \"https://api.openai.com/v1\")."
        )
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(
            f"Provider {provider_name!r} has an invalid base_url: {base_url!r}. "
            "It must be a full http(s) URL including scheme and host "
            "(e.g. base_url = \"https://api.openai.com/v1\"). "
            f"Fix it in [providers.{provider_name}] at {CONFIG_PATH}."
        )
    path = parsed.path.rstrip("/")
    ENDPOINT_SUFFIXES = (
        "/chat/completions", "/completions", "/responses",
        "/embeddings", "/models",
    )
    for suffix in ENDPOINT_SUFFIXES:
        if path.endswith(suffix):
            root_path = path[: -len(suffix)] or "/"
            suggested = parsed._replace(path=root_path).geturl()
            raise RuntimeError(
                f"Provider {provider_name!r} base_url points at a full endpoint, "
                f"not the API root: {base_url!r}. codey appends the endpoint path "
                "itself, so set base_url to the API root instead "
                f"(e.g. base_url = \"{suggested}\"). "
                f"Fix it in [providers.{provider_name}] at {CONFIG_PATH}."
            )
    return base_url


@dataclass(frozen=True)
class MemoryConfig:
    """User-level long-term-memory toggles, from the top-level [memory] block.

    One setting per user; no per-provider override in v1.
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
class Provider:
    name: str
    api_key: str
    base_url: str
    model: str
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    compact_headroom: int = DEFAULT_COMPACT_HEADROOM

    @property
    def needs_api_key(self) -> bool:
        return not self.api_key or self.api_key == PLACEHOLDER_API_KEY


@dataclass
class ConfigFile:
    """In-memory view of ~/.config/codey/config.toml."""

    default_provider: str
    providers: dict[str, Provider]
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    @classmethod
    def load(cls) -> "ConfigFile":
        """Load from disk, bootstrapping from .env if the file doesn't exist."""
        load_dotenv()  # so env-var fallbacks/overrides work consistently

        if not CONFIG_PATH.exists():
            return cls._bootstrap_from_env()

        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)

        raw_providers = data.get("providers", {})
        if not raw_providers:
            raise RuntimeError(
                f"{CONFIG_PATH} has no [providers.*] entries. "
                "Add at least one provider."
            )

        providers: dict[str, Provider] = {}
        for name, body in raw_providers.items():
            providers[name] = Provider(
                name=name,
                api_key=body.get("api_key", ""),
                base_url=body.get("base_url", DEFAULT_BASE_URL),
                model=body.get("model", DEFAULT_MODEL),
                context_window=int(body.get("context_window", DEFAULT_CONTEXT_WINDOW)),
                max_output_tokens=int(body.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)),
                compact_headroom=int(body.get("compact_headroom", DEFAULT_COMPACT_HEADROOM)),
            )

        default = data.get("default_provider") or next(iter(providers))
        if default not in providers:
            raise RuntimeError(
                f"default_provider = {default!r} not found in [providers.*]"
            )
        return cls(default_provider=default, providers=providers,
                   memory=_parse_memory(data.get("memory", {})))

    @classmethod
    def _bootstrap_from_env(cls) -> "ConfigFile":
        """First-run: seed config.toml from CODEY_* env vars, then load it."""
        api_key = os.environ.get("CODEY_API_KEY")
        if not api_key:
            raise RuntimeError(
                f"No config at {CONFIG_PATH} and CODEY_API_KEY is unset.\n"
                f"Either create {CONFIG_PATH} with a [providers.*] block, "
                "or set CODEY_API_KEY in .env so codey can bootstrap one."
            )

        base_url = os.environ.get("CODEY_BASE_URL", DEFAULT_BASE_URL)
        model = os.environ.get("CODEY_MODEL", DEFAULT_MODEL)

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            'default_provider = "default"\n'
            "\n"
            "[providers.default]\n"
            f'base_url = "{base_url}"\n'
            f'api_key  = "{api_key}"\n'
            f'model    = "{model}"\n'
        )
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
        print(f"(created {CONFIG_PATH} from .env — edit it to add more providers)")
        return cls.load()

    def resolve(self, name: str | None = None) -> Provider:
        """Pick the active provider. Provider fields win; env vars fill empty fields."""
        chosen = name or os.environ.get("CODEY_PROVIDER") or self.default_provider
        if chosen not in self.providers:
            raise RuntimeError(
                f"Unknown provider {chosen!r}. "
                f"Available: {', '.join(sorted(self.providers)) or '(none)'}"
            )
        provider = self.providers[chosen]

        # env vars only fill in empty provider fields
        resolved = replace(
            provider,
            api_key=provider.api_key or os.environ.get("CODEY_API_KEY", ""),
            base_url=provider.base_url or os.environ.get("CODEY_BASE_URL", DEFAULT_BASE_URL),
            model=provider.model or os.environ.get("CODEY_MODEL", DEFAULT_MODEL),
        )
        _validate_base_url(resolved.name, resolved.base_url)
        return resolved


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


def set_provider_api_key(provider_name: str, api_key: str) -> None:
    """Rewrite api_key for one [providers.<name>] block in config.toml, in place.

    Only the api_key line of that provider is touched; everything else stays.
    No-op if the file or block is absent.
    """
    if not CONFIG_PATH.exists():
        return
    lines = CONFIG_PATH.read_text().splitlines()
    header = f"[providers.{provider_name}]"
    in_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_block = stripped == header
            continue
        if in_block and stripped.startswith("api_key"):
            lines[i] = f'api_key  = "{api_key}"'
            break
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
