"""Tests for codey.memory.registry."""
from __future__ import annotations

from pathlib import Path

from codey.memory.registry import MemoryRegistry


def _write(path: Path, *, name: str, desc: str, type_: str = "fact",
           body: str = "b") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        f"type: {type_}\n"
        "created_at: t\nupdated_at: t\nsource_session: s\n---\n\n"
        f"{body}\n"
    )
    path.write_text(text)


def test_registry_merges_two_tiers_project_wins(tmp_path: Path) -> None:
    g = tmp_path / "global"
    p = tmp_path / "project"
    _write(g / "shared.md", name="shared", desc="from global", body="GLOBAL")
    _write(g / "g_only.md", name="g_only", desc="g only")
    _write(p / "shared.md", name="shared", desc="from project", body="PROJECT")
    _write(p / "p_only.md", name="p_only", desc="p only")

    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "audit.jsonl")
    assert sorted(reg.names()) == ["g_only", "p_only", "shared"]
    shared = reg.get("shared")
    assert shared is not None and shared.body.strip() == "PROJECT"
    assert shared.scope == "project"


def test_registry_list_meta_empty_when_no_entries(tmp_path: Path) -> None:
    reg = MemoryRegistry.scan(global_root=tmp_path / "x", project_root=tmp_path / "y",
                              audit_log_path=tmp_path / "audit.jsonl")
    assert reg.list_meta() == ""


def test_registry_list_meta_includes_header_and_bullets(tmp_path: Path) -> None:
    g = tmp_path / "g"
    _write(g / "rule_a.md", name="rule_a", desc="the A rule")
    reg = MemoryRegistry.scan(global_root=g, project_root=tmp_path / "p",
                              audit_log_path=tmp_path / "audit.jsonl")
    meta = reg.list_meta()
    assert "## Memory" in meta
    assert "**rule_a** — the A rule" in meta


def test_registry_skips_malformed_files_and_audits(tmp_path: Path) -> None:
    g = tmp_path / "g"
    g.mkdir(parents=True, exist_ok=True)
    (g / "good.md").write_text(
        "---\nname: good\ndescription: ok\ntype: t\n"
        "created_at: t\nupdated_at: t\nsource_session: s\n---\n\nbody\n"
    )
    (g / "bad.md").write_text("no frontmatter here\n")
    audit = tmp_path / "audit.jsonl"
    reg = MemoryRegistry.scan(global_root=g, project_root=tmp_path / "p",
                              audit_log_path=audit)
    assert "good" in reg.names()
    assert "bad" not in reg.names()
    assert audit.exists()
    assert "memory_invalid" in audit.read_text()
