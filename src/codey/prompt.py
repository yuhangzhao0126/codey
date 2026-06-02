"""System prompt assembly.

The prompt is built by *appending* up to three layers, in order:

    1. default  — src/codey/prompts/system.md  (ships with the package)
    2. user     — ~/.config/codey/system.md    (personal customization)
    3. project  — ./codey.md                   (per-repo context, like CLAUDE.md)

Each present layer is appended with a blank line between layers. Missing layers
are skipped silently. The default layer is always present.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

USER_PROMPT_PATH = Path.home() / ".config" / "codey" / "system.md"
PROJECT_PROMPT_PATH = Path("codey.md")


def _read_default() -> str:
    return files("codey.prompts").joinpath("system.md").read_text(encoding="utf-8")


def _read_if_exists(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return text or None


def build_system_prompt(cwd: Path | None = None) -> str:
    """Concatenate default + user + project layers, separated by blank lines."""
    project_path = (cwd or Path.cwd()) / PROJECT_PROMPT_PATH

    layers = [
        _read_default().strip(),
        _read_if_exists(USER_PROMPT_PATH),
        _read_if_exists(project_path),
    ]
    return "\n\n".join(layer for layer in layers if layer)
