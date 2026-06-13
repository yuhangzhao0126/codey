"""Tests for codey.memory.select."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.memory.models import Memory
from codey.memory.registry import MemoryRegistry
from codey.memory.select import pick_relevant, pick_relevant_keyword


def _mk(name: str, desc: str) -> Memory:
    return Memory(
        name=name, description=desc, type="t", body="b",
        created_at="t", updated_at="t", source_session="s",
        scope="global", source_path=Path("/tmp/x.md"),
    )


def test_keyword_fallback_picks_overlap() -> None:
    reg = MemoryRegistry()
    reg._memories = {
        "uses_pnpm":  _mk("uses_pnpm",  "prefer pnpm over npm"),
        "uses_tabs":  _mk("uses_tabs",  "indent with tabs"),
        "lint_rules": _mk("lint_rules", "ruff settings"),
    }
    picked = pick_relevant_keyword("install a new pnpm package", reg, k=5)
    assert "uses_pnpm" in picked
    assert "uses_tabs" not in picked


def test_keyword_fallback_returns_empty_when_no_match() -> None:
    reg = MemoryRegistry()
    reg._memories = {"uses_pnpm": _mk("uses_pnpm", "prefer pnpm over npm")}
    picked = pick_relevant_keyword("draw a unicorn picture", reg, k=5)
    assert picked == []


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class FakeCompletions:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def create(self, **_kw) -> _FakeResp:
        return _FakeResp(self._payload)


class FakeChat:
    def __init__(self, payload: str) -> None:
        self.completions = FakeCompletions(payload)


class FakeClient:
    def __init__(self, payload: str) -> None:
        self.chat = FakeChat(payload)


@pytest.mark.asyncio
async def test_pick_relevant_llm_returns_names() -> None:
    reg = MemoryRegistry()
    reg._memories = {"a": _mk("a", "x"), "b": _mk("b", "y")}
    picked = await pick_relevant("any", reg, client=FakeClient('["a"]'), model="m")
    assert picked == ["a"]


@pytest.mark.asyncio
async def test_pick_relevant_falls_back_on_bad_json() -> None:
    reg = MemoryRegistry()
    reg._memories = {"tabs_rule": _mk("tabs_rule", "tabs only please")}
    picked = await pick_relevant("set up tabs", reg,
                                 client=FakeClient("not json"), model="m")
    assert picked == ["tabs_rule"]


@pytest.mark.asyncio
async def test_pick_relevant_drops_unknown_names() -> None:
    reg = MemoryRegistry()
    reg._memories = {"a": _mk("a", "x")}
    picked = await pick_relevant("q", reg,
                                 client=FakeClient('["a","bogus"]'), model="m")
    assert picked == ["a"]


@pytest.mark.asyncio
async def test_pick_relevant_respects_k_cap() -> None:
    reg = MemoryRegistry()
    reg._memories = {n: _mk(n, "d") for n in ["a", "b", "c", "d", "e", "f"]}
    picked = await pick_relevant("q", reg,
                                 client=FakeClient('["a","b","c","d","e","f"]'),
                                 model="m", k=3)
    assert len(picked) == 3
