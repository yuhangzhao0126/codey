"""TUI tests for /skills slash command."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Input

from codey.skills import Skill, SkillRegistry
from codey.ui import CodeyApp

pytestmark = pytest.mark.usefixtures("temp_config")


def _transcript_text(app: CodeyApp) -> str:
    parts: list[str] = []
    for line in app.transcript.lines:
        try:
            parts.append(line.text)
        except AttributeError:
            parts.append(str(line))
    return "\n".join(parts)


async def _submit(pilot, text: str) -> None:
    inp = pilot.app.query_one(Input)
    inp.focus()
    inp.value = text
    await pilot.pause()
    await inp.action_submit()
    await pilot.pause()


def _seed(app: CodeyApp, skills: list[Skill]) -> None:
    """Replace the session's skill registry with a hand-built one for the test."""
    reg = SkillRegistry()
    for s in skills:
        reg._skills[s.name] = s
    app.session.skills = reg


def _mk(name: str, description: str, tier: str = "user") -> Skill:
    return Skill(name=name, description=description, body="body",
                 source_path=Path(f"/fake/{name}/SKILL.md"), tier=tier)  # type: ignore[arg-type]


async def test_skills_empty_says_so():
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        _seed(app, [])
        await _submit(pilot, "/skills")
        text = _transcript_text(app)
        assert "no skills" in text.lower()


async def test_skills_lists_each_with_tier_and_description():
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        _seed(app, [
            _mk("alpha", "Alpha skill description.", tier="package"),
            _mk("beta",  "Beta skill description.",  tier="user"),
            _mk("gamma", "Gamma skill description.", tier="project"),
        ])
        await _submit(pilot, "/skills")
        text = _transcript_text(app)
        # All three names + descriptions present.
        for name in ("alpha", "beta", "gamma"):
            assert name in text
        assert "Alpha skill description." in text
        assert "Beta skill description." in text
        assert "Gamma skill description." in text
        # Tier visible so the user knows where each one comes from.
        assert "package" in text
        assert "user" in text
        assert "project" in text


async def test_skills_appears_in_help():
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/help")
        text = _transcript_text(app)
        assert "/skills" in text


async def test_skil_resolves_to_skills():
    """Substring matcher resolves /skil to /skills uniquely."""
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        _seed(app, [_mk("foo", "bar")])
        await _submit(pilot, "/skil")
        text = _transcript_text(app)
        assert "foo" in text
        assert "bar" in text
