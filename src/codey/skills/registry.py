"""SkillRegistry: scan three tiers, hold parsed skills in memory.

Override precedence: project > user > package (most specific wins). Every
override and every malformed skill writes a JSONL line to the audit log
file (`~/.cache/codey/calls.jsonl` by default — same file the audit_log
hook uses, so there's one unified trail).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..hooks.builtin.audit_log import DEFAULT_LOG_PATH
from .io import parse_skill_md
from .models import Skill, Tier


_INDEX_PREAMBLE = (
    "## Available skills\n\n"
    "You can load any of these on demand by calling `load_skill` with the "
    "skill's name. Each skill's body is hidden until you load it — load "
    "only what's relevant to the current task.\n"
)


@dataclass
class SkillRegistry:
    _skills: dict[str, Skill] = field(default_factory=dict)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills.keys())

    def list_meta(self) -> str:
        if not self._skills:
            return ""
        bullets = [
            f"- **{name}** — {self._skills[name].description}"
            for name in self.names()
        ]
        return _INDEX_PREAMBLE + "\n" + "\n".join(bullets)

    @classmethod
    def scan(
        cls,
        *,
        package_root: Path,
        user_root: Path,
        project_root: Path,
        audit_log_path: Path | None = None,
    ) -> "SkillRegistry":
        log_path = audit_log_path or DEFAULT_LOG_PATH
        reg = cls()
        # Order matters: scan package first, then user, then project.
        # Later tiers override earlier ones; we record the displaced skill
        # to the audit log as `skill_override`.
        for tier, root in (("package", package_root),
                           ("user", user_root),
                           ("project", project_root)):
            reg._scan_one_tier(tier, root, log_path)
        return reg

    def _scan_one_tier(self, tier: Tier, root: Path, log_path: Path) -> None:
        if not root.is_dir():
            return
        seen_this_tier: set[str] = set()
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                _audit(log_path, {"event": "skill_invalid",
                                  "path": str(skill_md),
                                  "tier": tier,
                                  "reason": "no SKILL.md"})
                continue
            try:
                text = skill_md.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                _audit(log_path, {"event": "skill_invalid",
                                  "path": str(skill_md),
                                  "tier": tier,
                                  "reason": "not utf-8"})
                continue
            except OSError as e:
                _audit(log_path, {"event": "skill_invalid",
                                  "path": str(skill_md),
                                  "tier": tier,
                                  "reason": f"read error: {type(e).__name__}"})
                continue
            parsed = parse_skill_md(text, dir_name=child.name,
                                    source_path=skill_md, tier=tier)
            if isinstance(parsed, str):
                _audit(log_path, {"event": "skill_invalid",
                                  "path": str(skill_md),
                                  "tier": tier,
                                  "reason": parsed})
                continue
            if child.name in seen_this_tier:
                _audit(log_path, {"event": "skill_invalid",
                                  "path": str(skill_md),
                                  "tier": tier,
                                  "reason": "duplicate name"})
                continue
            seen_this_tier.add(child.name)
            existing = self._skills.get(child.name)
            if existing is not None:
                _audit(log_path, {"event": "skill_override",
                                  "name": child.name,
                                  "winner_tier": tier,
                                  "winner_path": str(skill_md),
                                  "loser_tier": existing.tier,
                                  "loser_path": str(existing.source_path)})
            self._skills[child.name] = parsed


def _audit(log_path: Path, entry: dict) -> None:
    """Append one JSONL line. Failures are swallowed (same as audit_log hook)."""
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), **entry}
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
