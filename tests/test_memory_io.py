"""Tests for codey.memory.io + codey.memory.models."""
from __future__ import annotations

from pathlib import Path

from codey.memory.io import (
    MEMORY_INDEX_FILENAME,
    parse_memory_md,
    rebuild_index,
    serialize_memory,
    write_memory,
)
from codey.memory.models import Memory


def test_memory_dataclass_construct() -> None:
    m = Memory(
        name="user_prefers_tabs",
        description="use tabs not spaces",
        type="preference",
        body="Always tabs.",
        created_at="2026-06-13T14:00:00",
        updated_at="2026-06-13T14:00:00",
        source_session="sid123",
        scope="global",
        source_path=Path("/tmp/x.md"),
    )
    assert m.name == "user_prefers_tabs"
    assert m.scope == "global"


def test_parse_valid_memory(tmp_path: Path) -> None:
    text = (
        "---\n"
        "name: user_prefers_tabs\n"
        "description: tabs not spaces\n"
        "type: preference\n"
        "created_at: 2026-06-13T14:00:00\n"
        "updated_at: 2026-06-13T14:00:00\n"
        "source_session: abc12345\n"
        "---\n"
        "\n"
        "Always use tabs.\n"
    )
    p = tmp_path / "user_prefers_tabs.md"
    p.write_text(text)
    m = parse_memory_md(text, filename_stem="user_prefers_tabs",
                       source_path=p, scope="global")
    assert not isinstance(m, str), m
    assert m.name == "user_prefers_tabs"
    assert m.description == "tabs not spaces"
    assert m.type == "preference"
    assert m.body.strip() == "Always use tabs."


def test_parse_rejects_name_mismatch(tmp_path: Path) -> None:
    text = (
        "---\nname: other\ndescription: x\ntype: t\n"
        "created_at: t\nupdated_at: t\nsource_session: s\n---\nbody\n"
    )
    p = tmp_path / "wrong.md"
    p.write_text(text)
    result = parse_memory_md(text, filename_stem="wrong",
                             source_path=p, scope="global")
    assert isinstance(result, str)
    assert "name mismatch" in result


def test_parse_rejects_missing_description(tmp_path: Path) -> None:
    text = "---\nname: x\ntype: t\ncreated_at: t\nupdated_at: t\nsource_session: s\n---\nbody\n"
    p = tmp_path / "x.md"
    p.write_text(text)
    result = parse_memory_md(text, filename_stem="x", source_path=p, scope="global")
    assert isinstance(result, str) and "description" in result


def test_serialize_memory_roundtrip(tmp_path: Path) -> None:
    m = Memory(
        name="a", description="a desc", type="preference",
        body="line 1\nline 2", created_at="t1", updated_at="t2",
        source_session="sid", scope="global", source_path=tmp_path / "a.md",
    )
    text = serialize_memory(m)
    parsed = parse_memory_md(text, filename_stem="a",
                             source_path=tmp_path / "a.md", scope="global")
    assert not isinstance(parsed, str)
    assert parsed.body == "line 1\nline 2"


def test_write_and_rebuild_index(tmp_path: Path) -> None:
    m1 = Memory(
        name="a", description="a desc", type="preference",
        body="body a", created_at="t", updated_at="t",
        source_session="s", scope="global", source_path=tmp_path / "a.md",
    )
    m2 = Memory(
        name="b", description="b desc", type="fact",
        body="body b", created_at="t", updated_at="t",
        source_session="s", scope="global", source_path=tmp_path / "b.md",
    )
    write_memory(m1, tier_root=tmp_path)
    write_memory(m2, tier_root=tmp_path)
    assert (tmp_path / "a.md").exists()
    assert (tmp_path / "b.md").exists()

    rebuild_index(tier_root=tmp_path)
    idx = (tmp_path / MEMORY_INDEX_FILENAME).read_text()
    assert "**a**" in idx and "a desc" in idx
    assert "**b**" in idx and "b desc" in idx
