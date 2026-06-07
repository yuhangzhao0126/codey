"""Parse a SKILL.md file into a Skill, or return a string reason on failure.

Strict-subset of Claude Code's SKILL.md format:

    ---
    name: <optional, must equal dir name if present>
    description: <required, one line OR `|` block scalar across many lines>
    <other keys are ignored — forward compat with Claude Code skills>
    ---

    <body — required, non-empty after strip>

We don't use pyyaml: the frontmatter is always flat `key: value` lines, plus
one shape of block scalar (`key: |` followed by indented continuation lines)
because real Claude Code skills lean on it for multi-line descriptions.
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
    i = 1
    while i < len(lines):
        raw = lines[i]
        if raw.strip() == "---":
            end_idx = i
            break
        if ":" not in raw:
            i += 1
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "|" or val == ">":
            # YAML block scalar: gather subsequent indented lines until the
            # indentation drops back to column 0 (or we hit the closing ---).
            collected: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if nxt.strip() == "---":
                    break
                # Block-scalar continuation: line is indented or blank.
                if nxt and not nxt[0].isspace():
                    break
                collected.append(nxt.strip())
                j += 1
            # `|` preserves newlines logically; `>` folds to spaces. Since we
            # render the description as a single index bullet either way,
            # join with spaces (skipping empty lines).
            fm[key] = " ".join(c for c in collected if c)
            i = j
            continue
        fm[key] = val
        i += 1

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
