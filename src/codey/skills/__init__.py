"""Skill system: discover SKILL.md files from package/user/project tiers
and let the model load bodies on demand.

See docs/2026-06-07-skills-design.md for the design.
"""

from __future__ import annotations

from .models import Skill, Tier
from .registry import SkillRegistry

__all__ = ["Skill", "SkillRegistry", "Tier"]
