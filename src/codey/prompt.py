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
    from .memory import MemoryRegistry
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


def _memory_layer(memory: "MemoryRegistry | None") -> str | None:
    if memory is None:
        return None
    meta = memory.list_meta().strip()
    return meta or None


def build_system_prompt(
    cwd: Path | None = None,
    skills: "SkillRegistry | None" = None,
    memory: "MemoryRegistry | None" = None,
) -> str:
    """Concatenate default + user + project + skills + memory layers, separated by blank lines."""
    project_path = (cwd or Path.cwd()) / PROJECT_PROMPT_PATH

    layers = [
        _read_default().strip(),
        _read_if_exists(USER_PROMPT_PATH),
        _read_if_exists(project_path),
        _skills_layer(skills),
        _memory_layer(memory),
    ]
    return "\n\n".join(layer for layer in layers if layer)


def build_subagent_system_prompt(
    description: str,
    cwd: Path | None = None,
    skills: "SkillRegistry | None" = None,
    memory: "MemoryRegistry | None" = None,
) -> str:
    """System prompt for a sub-agent. Layers: default sub-agent → task line
    → user overlay → project overlay → skills index → memory index."""
    default = files("codey.prompts").joinpath("subagent.md").read_text(encoding="utf-8").strip()
    task_line = f"Your task (from the parent agent): {description.strip()}"
    project_path = (cwd or Path.cwd()) / PROJECT_PROMPT_PATH

    layers = [
        default,
        task_line,
        _read_if_exists(USER_PROMPT_PATH),
        _read_if_exists(project_path),
        _skills_layer(skills),
        _memory_layer(memory),
    ]
    return "\n\n".join(layer for layer in layers if layer)
