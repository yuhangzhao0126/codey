"""Parse a SKILL.md file into a Skill, or return a string reason on failure.

Strict-subset of Claude Code's SKILL.md format:

    ---
    name: <optional, must equal dir name if present>
    description: <required, one line>
    <other keys are ignored — forward compat with Claude Code skills>
    ---

    <body — required, non-empty after strip>

We don't use pyyaml: the frontmatter is always flat `key: value` lines.
"""

from __future__ import annotations

from pathlib import Path

from .models import Skill, Tier


def parse_skill_md(text: str, *, dir_name: str, source_path: Path, tier: Tier) -> Skill | str:
    """Parse SKILL.md `text`. Returns a Skill on success or a short error string."""
    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        return "no frontmatter"

    fm: dict[str, str] = {}
    end_idx: int | None = None
    for i, raw in enumerate(lines[1:], start=1):
        if raw.strip() == "---":
            end_idx = i
            break
        if ":" not in raw:
            # blank / non-key lines are ignored; the absence of a closing
            # `---` is what we ultimately error on.
            continue
        key, _, val = raw.partition(":")
        fm[key.strip()] = val.strip()

    if end_idx is None:
        return "unclosed frontmatter"

    description = fm.get("description", "").strip()
    if not description:
        return "missing description"

    name_field = fm.get("name", "").strip()
    if name_field and name_field != dir_name:
        return "name mismatch"

    body = "\n".join(lines[end_idx + 1:]).strip()
    if not body:
        return "empty body"

    return Skill(
        name=dir_name,
        description=description,
        body=body,
        source_path=source_path,
        tier=tier,
    )
