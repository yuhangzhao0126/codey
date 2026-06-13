"""Tests for codey.tools.load_memory + codey.tools.remember_this."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.memory.registry import MemoryRegistry
from codey.memory.store import MemoryStore
from codey.tools.load_memory import LoadMemoryTool
from codey.tools.remember_this import RememberThisTool


def _seed(root: Path, name: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: a desc\n"
        "type: t\ncreated_at: t\nupdated_at: t\nsource_session: s\n---\n\nBODY-OF-MEMORY\n"
    )


@pytest.mark.asyncio
async def test_load_memory_returns_body(tmp_path: Path) -> None:
    g = tmp_path / "g"
    _seed(g, "alpha")
    reg = MemoryRegistry.scan(global_root=g, project_root=tmp_path / "p",
                              audit_log_path=tmp_path / "audit.jsonl")
    tool = LoadMemoryTool(registry=reg)
    out = await tool.run({"name": "alpha"})
    assert "BODY-OF-MEMORY" in out


@pytest.mark.asyncio
async def test_load_memory_error_when_missing(tmp_path: Path) -> None:
    reg = MemoryRegistry.scan(global_root=tmp_path / "g", project_root=tmp_path / "p",
                              audit_log_path=tmp_path / "audit.jsonl")
    tool = LoadMemoryTool(registry=reg)
    out = await tool.run({"name": "nope"})
    assert out.startswith("error:")


@pytest.mark.asyncio
async def test_remember_this_writes_entry(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "audit.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "audit.jsonl")
    tool = RememberThisTool(registry=reg, store=store, session_id="sid",
                            default_scope="project")
    out = await tool.run({
        "name": "use_pnpm",
        "description": "prefer pnpm over npm",
        "body": "Always reach for pnpm in this repo.",
        "type": "preference",
    })
    assert out.startswith("ok:")
    assert (p / "use_pnpm.md").exists()
    assert reg.get("use_pnpm") is not None


@pytest.mark.asyncio
async def test_remember_this_validates_name(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "audit.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "audit.jsonl")
    tool = RememberThisTool(registry=reg, store=store, session_id="sid",
                            default_scope="project")
    out = await tool.run({"name": "", "description": "d", "body": "b"})
    assert out.startswith("error:")


@pytest.mark.asyncio
async def test_remember_this_respects_scope(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "audit.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "audit.jsonl")
    tool = RememberThisTool(registry=reg, store=store, session_id="sid",
                            default_scope="project")
    out = await tool.run({
        "name": "global_rule", "description": "g", "body": "g",
        "scope": "global",
    })
    assert out.startswith("ok:")
    assert (g / "global_rule.md").exists()
    assert not (p / "global_rule.md").exists()
