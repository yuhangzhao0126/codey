"""System prompt assembly.

The prompt is built by *appending* up to four layers, in order:

    1. default       — src/codey/prompts/system.md  (ships with the package)
    2. user          — ~/.config/codey/system.md    (personal customization)
    3. project       — ./codey.md                   (per-repo context, like CLAUDE.md)
    4. skills index  — `## Available skills` list from the SkillRegistry

Each present layer is appended with a blank line between layers. Missing layers
are skipped silently. The default layer is always present; the skills layer is
appended only when a non-empty SkillRegistry is provided.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .skills import SkillRegistry

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


def _skills_layer(skills: "SkillRegistry | None") -> str | None:
    if skills is None:
        return None
    meta = skills.list_meta().strip()
    return meta or None


def build_system_prompt(
    cwd: Path | None = None,
    skills: "SkillRegistry | None" = None,
) -> str:
    """Concatenate default + user + project + skills layers, separated by blank lines."""
    project_path = (cwd or Path.cwd()) / PROJECT_PROMPT_PATH

    layers = [
        _read_default().strip(),
        _read_if_exists(USER_PROMPT_PATH),
        _read_if_exists(project_path),
        _skills_layer(skills),
    ]
    return "\n\n".join(layer for layer in layers if layer)


def build_subagent_system_prompt(
    description: str,
    cwd: Path | None = None,
    skills: "SkillRegistry | None" = None,
) -> str:
    """System prompt for a sub-agent.

    Layers, in order:
      1. default sub-agent prompt — src/codey/prompts/subagent.md
      2. a one-line description of THIS sub-agent's task
      3. user overlay — ~/.config/codey/system.md (same one the parent uses)
      4. project overlay — ./codey.md (same one the parent uses)
      5. skills index — same registry the parent sees, so children can load too

    Layers 1 and 2 are always present. Missing 3, 4, 5 are skipped silently.
    Per-repo conventions in codey.md apply to children too — sub-agents work
    on the same codebase under the same rules.
    """
    default = files("codey.prompts").joinpath("subagent.md").read_text(encoding="utf-8").strip()
    task_line = f"Your task (from the parent agent): {description.strip()}"
    project_path = (cwd or Path.cwd()) / PROJECT_PROMPT_PATH

    layers = [
        default,
        task_line,
        _read_if_exists(USER_PROMPT_PATH),
        _read_if_exists(project_path),
        _skills_layer(skills),
    ]
    return "\n\n".join(layer for layer in layers if layer)
