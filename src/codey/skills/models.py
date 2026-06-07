"""Skill model.

A Skill is the in-memory representation of one SKILL.md, populated by the
loader (`SkillRegistry.scan`). The body has its frontmatter stripped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Tier = Literal["package", "user", "project"]


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    source_path: Path
    tier: Tier
