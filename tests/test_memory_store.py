"""Tests for codey.memory.store."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codey.memory.models import Memory
from codey.memory.store import MemoryStore


def _mk(name: str = "m1", desc: str = "d", type_: str = "preference") -> Memory:
    return Memory(
        name=name, description=desc, type=type_, body="b",
        created_at="2026-06-13T14:00:00",
        updated_at="2026-06-13T14:00:00",
        source_session="s", scope="global", source_path=Path("/x.md"),
    )


@pytest.mark.asyncio
async def test_store_write_creates_file_and_rebuilds_index(tmp_path: Path) -> None:
    g = tmp_path / "global"
    p = tmp_path / "project"
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "audit.jsonl")
    await store.write(_mk(name="x", desc="x desc"), scope="global", source="tool")
    assert (g / "x.md").exists()
    assert (g / "MEMORY.md").exists()
    assert "**x**" in (g / "MEMORY.md").read_text()


@pytest.mark.asyncio
async def test_store_delete_rebuilds_index(tmp_path: Path) -> None:
    g = tmp_path / "global"
    store = MemoryStore(global_root=g, project_root=tmp_path / "p",
                        audit_log_path=tmp_path / "a.jsonl")
    await store.write(_mk(name="x", desc="x desc"), scope="global", source="tool")
    await store.delete("x", scope="global", source="tool")
    assert not (g / "x.md").exists()
    idx = (g / "MEMORY.md").read_text()
    assert "**x**" not in idx


@pytest.mark.asyncio
async def test_store_audit_lines_emitted(tmp_path: Path) -> None:
    g = tmp_path / "g"
    audit = tmp_path / "audit.jsonl"
    store = MemoryStore(global_root=g, project_root=tmp_path / "p",
                        audit_log_path=audit)
    await store.write(_mk(name="x", desc="d"), scope="global", source="extract")
    events = [json.loads(l) for l in audit.read_text().splitlines()]
    assert any(e.get("event") == "memory_write" for e in events)
    assert any(e.get("source") == "extract" for e in events)


@pytest.mark.asyncio
async def test_store_project_scope_writes_to_project_root(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "a.jsonl")
    await store.write(_mk(name="y", desc="proj"), scope="project", source="tool")
    assert (p / "y.md").exists()
    assert not (g / "y.md").exists()
