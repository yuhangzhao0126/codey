"""Tests that the prompt builder includes/skips the memory layer correctly."""
from __future__ import annotations

from pathlib import Path

from codey.memory.registry import MemoryRegistry
from codey.prompt import build_subagent_system_prompt, build_system_prompt


def _seed(root: Path, name: str, desc: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        "type: t\ncreated_at: t\nupdated_at: t\nsource_session: s\n---\n\nbody\n"
    )


def test_prompt_includes_memory_index_when_registry_nonempty(tmp_path: Path) -> None:
    g = tmp_path / "g"
    _seed(g, "rule_a", "the A rule")
    reg = MemoryRegistry.scan(global_root=g, project_root=tmp_path / "p",
                              audit_log_path=tmp_path / "audit.jsonl")
    prompt = build_system_prompt(cwd=tmp_path, memory=reg)
    assert "## Memory" in prompt
    assert "**rule_a** — the A rule" in prompt


def test_prompt_skips_memory_layer_when_registry_empty(tmp_path: Path) -> None:
    reg = MemoryRegistry.scan(global_root=tmp_path / "g", project_root=tmp_path / "p",
                              audit_log_path=tmp_path / "audit.jsonl")
    prompt = build_system_prompt(cwd=tmp_path, memory=reg)
    assert "## Memory" not in prompt


def test_subagent_prompt_includes_memory_layer(tmp_path: Path) -> None:
    g = tmp_path / "g"
    _seed(g, "shared", "shared rule")
    reg = MemoryRegistry.scan(global_root=g, project_root=tmp_path / "p",
                              audit_log_path=tmp_path / "audit.jsonl")
    prompt = build_subagent_system_prompt(
        "investigate", cwd=tmp_path, memory=reg,
    )
    assert "## Memory" in prompt
    assert "**shared**" in prompt
