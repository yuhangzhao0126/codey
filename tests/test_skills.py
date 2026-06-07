"""Tests for the skill system: scan, override, parse, tool, prompt layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codey.skills import Skill, SkillRegistry
from codey.tools.load_skill import LoadSkillTool


def _write_skill(root: Path, name: str, description: str, body: str = "body text",
                 frontmatter_name: str | None = None,
                 extra_keys: dict[str, str] | None = None,
                 no_open: bool = False,
                 no_close: bool = False) -> Path:
    """Helper: scaffold a SKILL.md under root/<name>/. Returns the file path."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    if not no_open:
        lines.append("---")
    if frontmatter_name is not None:
        lines.append(f"name: {frontmatter_name}")
    if description is not None:
        lines.append(f"description: {description}")
    if extra_keys:
        for k, v in extra_keys.items():
            lines.append(f"{k}: {v}")
    if not no_close:
        lines.append("---")
    lines.append("")
    lines.append(body)
    path = d / "SKILL.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------- scan: discovery from each tier ----------

def test_scan_finds_package_skill(tmp_path: Path):
    pkg = tmp_path / "pkg"
    _write_skill(pkg, "foo", "A foo skill.")
    reg = SkillRegistry.scan(package_root=pkg, user_root=tmp_path / "nope_user",
                             project_root=tmp_path / "nope_proj")
    skill = reg.get("foo")
    assert skill is not None
    assert skill.name == "foo"
    assert skill.description == "A foo skill."
    assert skill.tier == "package"
    assert skill.body.strip() == "body text"


def test_scan_finds_user_skill(tmp_path: Path):
    user = tmp_path / "user"
    _write_skill(user, "bar", "A bar skill.")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope", user_root=user,
                             project_root=tmp_path / "nope2")
    assert reg.get("bar") is not None
    assert reg.get("bar").tier == "user"


def test_scan_finds_project_skill(tmp_path: Path):
    proj = tmp_path / "proj"
    _write_skill(proj, "baz", "A baz skill.")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope", user_root=tmp_path / "nope2",
                             project_root=proj)
    assert reg.get("baz") is not None
    assert reg.get("baz").tier == "project"


# ---------- override precedence ----------

def test_user_overrides_package(tmp_path: Path):
    pkg = tmp_path / "pkg"
    user = tmp_path / "user"
    audit_path = tmp_path / "audit.jsonl"
    _write_skill(pkg, "x", "pkg version")
    _write_skill(user, "x", "user version")

    reg = SkillRegistry.scan(package_root=pkg, user_root=user,
                             project_root=tmp_path / "nope",
                             audit_log_path=audit_path)
    skill = reg.get("x")
    assert skill.tier == "user"
    assert skill.description == "user version"
    # exactly one override entry written
    lines = [json.loads(line) for line in audit_path.read_text().splitlines() if line]
    overrides = [l for l in lines if l.get("event") == "skill_override"]
    assert len(overrides) == 1
    assert overrides[0]["name"] == "x"
    assert overrides[0]["winner_tier"] == "user"
    assert overrides[0]["loser_tier"] == "package"


def test_project_overrides_user_and_package(tmp_path: Path):
    pkg = tmp_path / "pkg"
    user = tmp_path / "user"
    proj = tmp_path / "proj"
    _write_skill(pkg, "y", "pkg version")
    _write_skill(user, "y", "user version")
    _write_skill(proj, "y", "proj version")

    reg = SkillRegistry.scan(package_root=pkg, user_root=user, project_root=proj)
    skill = reg.get("y")
    assert skill.tier == "project"
    assert skill.description == "proj version"


# ---------- validation: malformed skills are skipped + logged ----------

def test_missing_description_skipped(tmp_path: Path):
    user = tmp_path / "user"
    audit_path = tmp_path / "audit.jsonl"
    d = user / "broken"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\n---\nbody\n", encoding="utf-8")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2",
                             audit_log_path=audit_path)
    assert reg.get("broken") is None
    lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l]
    invalid = [l for l in lines if l.get("event") == "skill_invalid"]
    assert any(l["reason"] == "missing description" for l in invalid)


def test_name_mismatch_skipped(tmp_path: Path):
    user = tmp_path / "user"
    audit_path = tmp_path / "audit.jsonl"
    _write_skill(user, "actual", "desc", frontmatter_name="different")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2",
                             audit_log_path=audit_path)
    assert reg.get("actual") is None
    assert reg.get("different") is None
    lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l]
    assert any(l.get("reason") == "name mismatch" for l in lines)


def test_no_frontmatter_skipped(tmp_path: Path):
    user = tmp_path / "user"
    d = user / "raw"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("just a body, no frontmatter\n", encoding="utf-8")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    assert reg.get("raw") is None


def test_empty_body_skipped(tmp_path: Path):
    user = tmp_path / "user"
    audit_path = tmp_path / "audit.jsonl"
    d = user / "noop"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\ndescription: x\n---\n\n", encoding="utf-8")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2",
                             audit_log_path=audit_path)
    assert reg.get("noop") is None
    lines = [json.loads(l) for l in audit_path.read_text().splitlines() if l]
    assert any(l.get("reason") == "empty body" for l in lines)


def test_unknown_frontmatter_keys_ignored(tmp_path: Path):
    """Forward compat: extra Claude-Code fields don't break the loader."""
    user = tmp_path / "user"
    _write_skill(user, "compat", "Has extra fields.",
                 extra_keys={"allowed-tools": "Read Grep",
                             "disable-model-invocation": "true"})
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    assert reg.get("compat") is not None


def test_missing_skill_md_skipped(tmp_path: Path):
    """A directory without SKILL.md inside the skills root is just ignored."""
    user = tmp_path / "user"
    (user / "naked").mkdir(parents=True)
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    assert reg.get("naked") is None


def test_missing_root_is_fine(tmp_path: Path):
    """Nonexistent skill roots should not raise — just yield zero skills."""
    reg = SkillRegistry.scan(package_root=tmp_path / "nope1",
                             user_root=tmp_path / "nope2",
                             project_root=tmp_path / "nope3")
    assert reg.names() == []


# ---------- list_meta: the prompt-layer text ----------

def test_list_meta_format(tmp_path: Path):
    user = tmp_path / "user"
    _write_skill(user, "alpha", "Alpha skill.")
    _write_skill(user, "beta",  "Beta skill.")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    meta = reg.list_meta()
    assert "## Available skills" in meta
    assert "load_skill" in meta
    # both skills listed, sorted by name
    assert "- **alpha** — Alpha skill." in meta
    assert "- **beta** — Beta skill." in meta
    assert meta.index("alpha") < meta.index("beta")


def test_list_meta_empty_when_no_skills(tmp_path: Path):
    reg = SkillRegistry.scan(package_root=tmp_path / "nope1",
                             user_root=tmp_path / "nope2",
                             project_root=tmp_path / "nope3")
    assert reg.list_meta() == ""


# ---------- LoadSkillTool ----------

@pytest.mark.asyncio
async def test_load_skill_tool_returns_body(tmp_path: Path):
    user = tmp_path / "user"
    _write_skill(user, "hello", "Say hi.", body="# Hello\nDo a greeting.")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    tool = LoadSkillTool(skills=reg)
    out = await tool.run({"name": "hello"})
    assert out.strip() == "# Hello\nDo a greeting."
    # frontmatter NOT included
    assert "description:" not in out


@pytest.mark.asyncio
async def test_load_skill_tool_unknown_name(tmp_path: Path):
    user = tmp_path / "user"
    _write_skill(user, "real", "exists.")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    tool = LoadSkillTool(skills=reg)
    out = await tool.run({"name": "nope"})
    assert out.startswith("error:")
    assert "Available:" in out
    assert "real" in out


@pytest.mark.asyncio
async def test_load_skill_tool_empty_name(tmp_path: Path):
    reg = SkillRegistry.scan(package_root=tmp_path / "nope1",
                             user_root=tmp_path / "nope2",
                             project_root=tmp_path / "nope3")
    tool = LoadSkillTool(skills=reg)
    out = await tool.run({"name": ""})
    assert out.startswith("error:")


# ---------- prompt integration ----------

def test_build_system_prompt_includes_skills_index(tmp_path: Path):
    """build_system_prompt should append the skills_index as a 4th layer."""
    from codey.prompt import build_system_prompt
    user = tmp_path / "user"
    _write_skill(user, "indexed", "indexed skill.")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    prompt = build_system_prompt(skills=reg)
    assert "## Available skills" in prompt
    assert "indexed" in prompt


def test_build_subagent_system_prompt_includes_skills_index(tmp_path: Path):
    from codey.prompt import build_subagent_system_prompt
    user = tmp_path / "user"
    _write_skill(user, "kidskill", "for kids.")
    reg = SkillRegistry.scan(package_root=tmp_path / "nope",
                             user_root=user, project_root=tmp_path / "nope2")
    prompt = build_subagent_system_prompt(description="task", skills=reg)
    assert "## Available skills" in prompt
    assert "kidskill" in prompt


def test_build_system_prompt_without_skills_unchanged(tmp_path: Path):
    """Calling build_system_prompt with no/empty registry must not add the layer."""
    from codey.prompt import build_system_prompt
    out_none = build_system_prompt(cwd=tmp_path)
    assert "## Available skills" not in out_none

    empty = SkillRegistry.scan(package_root=tmp_path / "nope1",
                               user_root=tmp_path / "nope2",
                               project_root=tmp_path / "nope3")
    out_empty = build_system_prompt(cwd=tmp_path, skills=empty)
    assert "## Available skills" not in out_empty
