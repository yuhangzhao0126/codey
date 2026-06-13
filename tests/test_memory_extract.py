"""Tests for codey.memory.extract + consolidate + queue."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.core.messages import Message
from codey.memory.consolidate import Decision, targeted_consolidate
from codey.memory.extract import MemoryCandidate, propose_candidates
from codey.memory.queue import ack, drain, enqueue
from codey.memory.registry import MemoryRegistry
from codey.memory.store import MemoryStore


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
    def __init__(self, payloads: list[str]) -> None:
        self._payloads = list(payloads)

    async def create(self, **_kw) -> _FakeResp:
        return _FakeResp(self._payloads.pop(0) if self._payloads else "[]")


class FakeChat:
    def __init__(self, payloads: list[str]) -> None:
        self.completions = FakeCompletions(payloads)


class FakeClient:
    def __init__(self, payloads: list[str]) -> None:
        self.chat = FakeChat(payloads)


# ---------- propose_candidates ----------

@pytest.mark.asyncio
async def test_propose_candidates_returns_parsed_list() -> None:
    payload = (
        '[{"name":"use_pnpm","description":"prefer pnpm","body":"Use pnpm.",'
        '"type":"preference","scope":"project"}]'
    )
    client = FakeClient([payload])
    msgs = [Message(role="user", content="we use pnpm here")]
    out = await propose_candidates(msgs, existing_index="", client=client, model="m")
    assert len(out) == 1 and out[0].name == "use_pnpm"


@pytest.mark.asyncio
async def test_propose_candidates_returns_empty_on_bad_json() -> None:
    client = FakeClient(["not json"])
    out = await propose_candidates([Message(role="user", content="x")],
                                   existing_index="", client=client, model="m")
    assert out == []


@pytest.mark.asyncio
async def test_propose_drops_secrets() -> None:
    payload = (
        '[{"name":"api_key","description":"the key",'
        '"body":"sk-secret123456789","type":"fact","scope":"global"}]'
    )
    client = FakeClient([payload])
    out = await propose_candidates([Message(role="user", content="x")],
                                   existing_index="", client=client, model="m")
    assert out == []


@pytest.mark.asyncio
async def test_propose_returns_empty_on_empty_turn() -> None:
    client = FakeClient(["[]"])
    out = await propose_candidates([], existing_index="", client=client, model="m")
    assert out == []


# ---------- targeted_consolidate ----------

@pytest.mark.asyncio
async def test_consolidate_writes_novel(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "a.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "a.jsonl")
    cand = MemoryCandidate(name="new_rule", description="d", body="b",
                           type="preference", scope="global")
    decision = await targeted_consolidate(
        cand, registry=reg, store=store, session_id="sid",
        client=FakeClient(['{"verdict":"NOVEL"}']), model="m",
    )
    assert decision == Decision.NOVEL
    assert (g / "new_rule.md").exists()


@pytest.mark.asyncio
async def test_consolidate_skips_duplicate(tmp_path: Path) -> None:
    g = tmp_path / "g"
    p = tmp_path / "p"
    g.mkdir(parents=True, exist_ok=True)
    (g / "rule.md").write_text(
        "---\nname: rule\ndescription: d\ntype: preference\n"
        "created_at: t\nupdated_at: t\nsource_session: s\n---\n\nbody\n"
    )
    reg = MemoryRegistry.scan(global_root=g, project_root=p,
                              audit_log_path=tmp_path / "a.jsonl")
    store = MemoryStore(global_root=g, project_root=p,
                        audit_log_path=tmp_path / "a.jsonl")
    cand = MemoryCandidate(name="rule", description="d", body="body",
                           type="preference", scope="global")
    decision = await targeted_consolidate(
        cand, registry=reg, store=store, session_id="sid",
        client=FakeClient(['{"verdict":"DUPLICATE"}']), model="m",
    )
    assert decision == Decision.DUPLICATE


# ---------- queue ----------

def test_queue_enqueue_and_drain(tmp_path: Path) -> None:
    q = tmp_path / "queue.jsonl"
    line1 = enqueue(q, payload={"turn_id": "t1", "session_id": "s"})
    enqueue(q, payload={"turn_id": "t2", "session_id": "s"})
    items = drain(q)
    assert len(items) == 2
    ack(q, line_id=line1)
    remaining = drain(q)
    assert len(remaining) == 1 and remaining[0]["turn_id"] == "t2"
