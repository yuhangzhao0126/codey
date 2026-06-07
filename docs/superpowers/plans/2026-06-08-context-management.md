# Context Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4-step proactive context-compaction pipeline plus a reactive (provider 413) retry path so codey can run long conversations without blowing past the model's context window.

**Architecture:** A new `src/codey/context/` package with pure-function steps (`tool_result_budget`, `snip_compact`, `micro_compact`, `llm_compact_history`) orchestrated from `Agent.run()` at the top of each model round. Reactive path catches `PromptTooLongError` mapped from provider errors and retries once with an aggressive summary+tail compact. Adds one new model-callable `compact` tool, a `/compact` slash command, three new `Profile` fields, and a `recent_reads` hook.

**Tech Stack:** Python 3.11, asyncio, OpenAI-compatible API via `openai.AsyncOpenAI`, pytest (~196 tests today, targeting ~225 after this plan).

**Spec:** `docs/2026-06-07-context-management-design.md`

---

## File Structure

**New files:**
- `src/codey/context/__init__.py` — re-exports public surface
- `src/codey/context/errors.py` — `PromptTooLongError` + provider error sniffer
- `src/codey/context/tokens.py` — `estimate(messages) -> int` (chars ÷ 4)
- `src/codey/context/transcripts.py` — disk I/O: tool-result persistence + snapshots
- `src/codey/context/budget.py` — step 1: `tool_result_budget`
- `src/codey/context/snip.py` — step 2: `snip_compact` + pair-boundary helpers
- `src/codey/context/micro.py` — step 3: `micro_compact`
- `src/codey/context/llm.py` — step 4: `llm_compact_history` + helpers
- `src/codey/context/reactive.py` — failure-path `reactive_compact`
- `src/codey/context/pipeline.py` — orchestrator: `run_proactive`, `run_proactive_force_summary`, `run_reactive`
- `src/codey/hooks/builtin/recent_reads.py` — PostToolUse hook tracking `read_file` paths
- `src/codey/tools/compact.py` — model-callable `compact` tool
- `tests/test_context_tokens.py`
- `tests/test_context_transcripts.py`
- `tests/test_context_budget.py`
- `tests/test_context_snip.py`
- `tests/test_context_micro.py`
- `tests/test_context_llm.py`
- `tests/test_context_reactive.py`
- `tests/test_context_pipeline.py`
- `tests/test_context_errors.py`
- `tests/test_recent_reads_hook.py`
- `tests/test_compact_tool.py`
- `tests/test_compact_slash.py`
- `tests/test_profile_context_fields.py`

**Modified files:**
- `src/codey/config.py` — `Profile` gains `context_window`, `max_output_tokens`, `compact_headroom` fields
- `src/codey/core/turn.py` — wire pipeline + reactive retry + break-on-compact
- `src/codey/core/streaming.py` — map provider errors to `PromptTooLongError`
- `src/codey/core/session.py` — set `session_id/_meta/_recent_reads` on Agent, register `CompactTool`, pass child thresholds
- `src/codey/hooks/builtin/__init__.py` — register `recent_reads` hook, accept agent ref
- `src/codey/ui/slash_commands.py` — add `/compact`
- `src/codey/ui/app.py` — add `_cmd_compact` handler
- `CLAUDE.md` — "Where things go" table entry for `context/`

---

## Conventions used throughout this plan

- Tests use `pytest`, `pytest.mark.asyncio` for async, `tmp_path` for filesystem isolation, `monkeypatch` for env / module-level constants. Follow the patterns in `tests/test_skills.py` and `tests/test_hooks.py`.
- After every step that creates or modifies code, run `uv run pytest` (or the per-test command shown) and verify the expected pass/fail before moving on.
- Commits use the existing project style: lowercase prefix, terse body, end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Constants like `TOOL_RESULT_BUDGET_BYTES`, `SNIP_THRESHOLD_MESSAGES`, etc. live at the top of their step's module.

---

## Phase 1 — Pure-function building blocks (no LLM, no wiring)

This phase adds the three pure proactive steps + token estimator + disk I/O + error types. Nothing in `turn.py` changes; the new package is dead code until phase 3 wires it up. Tests are exhaustive at this phase because every step is independently unit-testable.

### Task 1: Scaffold the `context/` package

**Files:**
- Create: `src/codey/context/__init__.py`

- [ ] **Step 1: Create the package marker**

Create `src/codey/context/__init__.py` with exactly this content:

```python
"""Context management for the agent loop.

A 4-step proactive compaction pipeline plus a reactive retry path. The
pipeline runs at the top of every model round inside Agent.run() and is
designed so cheap steps are no-ops on small histories.

Steps (in order):
  1. tool_result_budget — persist >200kb tool results to disk
  2. snip_compact       — trim middle of conversation past 50 messages
  3. micro_compact      — placeholder old tool results, keep last 5 bodies
  4. llm_compact_history — single API call summary (only past headroom)

Failure path:
  reactive_compact      — runs on PromptTooLongError, ≤1 retry per turn

See docs/2026-06-07-context-management-design.md for the full spec.
"""
from __future__ import annotations
```

- [ ] **Step 2: Verify the package imports cleanly**

Run: `uv run python -c "import codey.context"`
Expected: no output, exit 0

- [ ] **Step 3: Commit**

```bash
git add src/codey/context/__init__.py
git commit -m "$(cat <<'EOF'
context: scaffold package marker

First slice of the context-management pipeline. Subsequent commits will
add the token estimator, disk I/O, the four steps, and the orchestrator.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Token estimator (`context/tokens.py`)

**Files:**
- Create: `src/codey/context/tokens.py`
- Test: `tests/test_context_tokens.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_tokens.py`:

```python
"""Tests for the chars/4 token estimator."""
from __future__ import annotations

from codey.context.tokens import estimate
from codey.core.messages import Message


def test_estimate_empty_history():
    assert estimate([]) == 0


def test_estimate_single_user_message():
    msgs = [Message(role="user", content="hello world")]    # 11 chars / 4 = 2
    assert estimate(msgs) == 2


def test_estimate_assistant_with_tool_calls():
    msgs = [
        Message(
            role="assistant",
            content="ok",                                    # 2
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},  # 4 + 12 = 16
            }],
        ),
    ]
    # content(2) + name(4) + arguments(12) = 18  →  18 // 4 = 4
    assert estimate(msgs) == 4


def test_estimate_tool_message_with_name():
    msgs = [Message(role="tool", tool_call_id="x", name="bash",
                    content="x" * 100)]
    # content(100) + name(4) = 104  →  26
    assert estimate(msgs) == 26


def test_estimate_sums_all_messages():
    msgs = [
        Message(role="system", content="x" * 40),    # 10
        Message(role="user", content="y" * 80),      # 20
        Message(role="assistant", content="z" * 40), # 10
    ]
    assert estimate(msgs) == 40


def test_estimate_handles_none_content():
    msgs = [Message(role="assistant", content="", tool_calls=[
        {"id": "1", "type": "function",
         "function": {"name": "t", "arguments": ""}}
    ])]
    # content(0) + name(1) + arguments(0) = 1  →  0
    assert estimate(msgs) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_tokens.py -v`
Expected: ModuleNotFoundError or ImportError on `codey.context.tokens`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/tokens.py`:

```python
"""Cheap chars/4 token estimator.

Used to decide when to trigger the LLM-summary step. The pipeline pads
its threshold with `compact_headroom` (default 13000 tokens) so per-model
tokenizer variance doesn't matter — being within 10-20% of the real count
is good enough.

No external deps. Deterministic. Pure function over a history list.
"""
from __future__ import annotations

from ..core.messages import Message


def estimate(history: list[Message]) -> int:
    """Estimate the prompt token count for a list of Message objects."""
    total_chars = 0
    for m in history:
        if m.content:
            total_chars += len(m.content)
        if m.tool_calls:
            for tc in m.tool_calls:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name") or ""
                args = fn.get("arguments") or ""
                total_chars += len(name) + len(args)
        if m.name:
            total_chars += len(m.name)
    return total_chars // 4
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_tokens.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/tokens.py tests/test_context_tokens.py
git commit -m "$(cat <<'EOF'
context: token estimator (chars / 4)

Cheap deterministic estimator over Message lists. Counts content,
flattened tool_call name+arguments, and tool message name. The
compact_headroom (default 13k tokens) absorbs estimation error so
per-model tokenizer variance is not a real concern.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Errors module + provider sniffer (`context/errors.py`)

**Files:**
- Create: `src/codey/context/errors.py`
- Test: `tests/test_context_errors.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_errors.py`:

```python
"""Tests for the provider-error sniffer."""
from __future__ import annotations

import pytest

from codey.context.errors import PromptTooLongError, sniff


class FakeProviderError(Exception):
    """Stand-in for openai.BadRequestError / openai.APIStatusError."""
    def __init__(self, status_code: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


def test_sniff_recognizes_openai_context_length_exceeded():
    err = FakeProviderError(400, "This model's maximum context length is 128000 tokens",
                            code="context_length_exceeded")
    result = sniff(err)
    assert isinstance(result, PromptTooLongError)


def test_sniff_recognizes_anthropic_prompt_too_long():
    err = FakeProviderError(400, "prompt is too long: 250000 tokens > 200000 maximum")
    assert isinstance(sniff(err), PromptTooLongError)


def test_sniff_recognizes_http_413():
    err = FakeProviderError(413, "Request Entity Too Large")
    assert isinstance(sniff(err), PromptTooLongError)


def test_sniff_returns_none_on_unrelated_error():
    err = FakeProviderError(429, "Too Many Requests", code="rate_limit_exceeded")
    assert sniff(err) is None


def test_sniff_returns_none_on_random_exception():
    err = ValueError("nothing to do with the provider")
    assert sniff(err) is None


def test_prompt_too_long_carries_original():
    original = FakeProviderError(413, "too big")
    e = PromptTooLongError("too big", original=original)
    assert e.original is original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_errors.py -v`
Expected: ImportError on `codey.context.errors`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/errors.py`:

```python
"""Provider error sniffer: map heterogeneous provider errors to one type.

OpenAI returns 400 + code=context_length_exceeded. Anthropic-compatible
gateways return 400 with "prompt is too long" in the message. Some gateways
return HTTP 413. We catch and re-raise as PromptTooLongError so the reactive
path in turn.py has one exception type to handle.
"""
from __future__ import annotations


class PromptTooLongError(Exception):
    """Raised when the provider reports the prompt exceeded its limit."""
    def __init__(self, message: str, *, original: BaseException | None = None):
        super().__init__(message)
        self.original = original


# Substrings (case-insensitive) we treat as "prompt too long" signals.
_PROMPT_TOO_LONG_SUBSTRINGS = (
    "context length",          # OpenAI: "maximum context length is X"
    "context_length_exceeded", # OpenAI error code
    "prompt is too long",      # Anthropic
    "prompt too long",
    "maximum context",
    "too many tokens",
    "request entity too large",
)


def sniff(exc: BaseException) -> PromptTooLongError | None:
    """Return a PromptTooLongError if `exc` looks like a context-overflow
    error from any supported provider; otherwise None.

    Inspects:
      - exc.status_code (if present): 413 is a strong signal
      - exc.code        (if present): "context_length_exceeded"
      - str(exc), exc.message (if present): substring scan
    """
    status = getattr(exc, "status_code", None)
    if status == 413:
        return PromptTooLongError(str(exc) or "request entity too large", original=exc)

    code = getattr(exc, "code", None)
    if isinstance(code, str) and "context_length" in code.lower():
        return PromptTooLongError(str(exc) or "context length exceeded", original=exc)

    blobs = []
    msg = getattr(exc, "message", None)
    if isinstance(msg, str):
        blobs.append(msg.lower())
    blobs.append(str(exc).lower())
    haystack = " ".join(blobs)
    for needle in _PROMPT_TOO_LONG_SUBSTRINGS:
        if needle in haystack:
            return PromptTooLongError(str(exc) or needle, original=exc)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_errors.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/errors.py tests/test_context_errors.py
git commit -m "$(cat <<'EOF'
context: PromptTooLongError + provider error sniffer

One exception type the reactive path can catch. The sniffer covers
OpenAI (code=context_length_exceeded), Anthropic-compatible gateways
("prompt is too long"), and bare HTTP 413. Unrecognized errors return
None so the existing error path in Agent.run() handles them as today.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Disk I/O for persisted tool results and snapshots (`context/transcripts.py`)

**Files:**
- Create: `src/codey/context/transcripts.py`
- Test: `tests/test_context_transcripts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_transcripts.py`:

```python
"""Tests for tool-result persistence and history snapshot writers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from codey.context.transcripts import (
    persisted_root_for,
    snapshots_root_for,
    write_persisted_tool_result,
    write_history_snapshot,
)
from codey.core.messages import Message


def test_persisted_root_layout(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = persisted_root_for("abc12345")
    assert p == tmp_path / "transcripts" / "abc12345" / "tool_results"


def test_snapshots_root_layout(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = snapshots_root_for("abc12345")
    assert p == tmp_path / "transcripts" / "abc12345" / "snapshots"


def test_write_persisted_tool_result_creates_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = write_persisted_tool_result(
        session_id="sess1", call_id="call_x", tool_name="bash",
        body="hello world",
    )
    assert p.read_text() == "hello world"
    assert p.parent == tmp_path / "transcripts" / "sess1" / "tool_results"
    assert p.name == "call_x-bash.txt"


def test_write_persisted_tool_result_sanitizes_call_id(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = write_persisted_tool_result(
        session_id="s", call_id="weird/call:id", tool_name="ba sh",
        body="x",
    )
    # No slashes or other path-traversal in the filename.
    assert "/" not in p.name
    assert ":" not in p.name
    assert " " not in p.name


def test_write_history_snapshot_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    history = [
        Message(role="system", content="sys"),
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello",
                tool_calls=[{"id": "c1", "type": "function",
                             "function": {"name": "t", "arguments": "{}"}}]),
        Message(role="tool", tool_call_id="c1", name="t", content="ok"),
    ]
    p = write_history_snapshot(session_id="sess2", history=history, kind="proactive")
    assert p.exists()
    data = json.loads(p.read_text())
    assert isinstance(data, list)
    assert len(data) == 4
    assert data[0] == {"role": "system", "content": "sys"}
    assert data[2]["tool_calls"][0]["id"] == "c1"
    assert data[3]["tool_call_id"] == "c1"
    assert "proactive" in p.name


def test_write_history_snapshot_kind_in_filename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    p = write_history_snapshot(session_id="s", history=[], kind="reactive")
    assert "reactive" in p.name
    assert p.name.endswith(".json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_transcripts.py -v`
Expected: ImportError on `codey.context.transcripts`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/transcripts.py`:

```python
"""Disk I/O for context-management spill files.

Two artifact types:

  - Persisted tool results: raw text bodies, one per file, under
    ~/.cache/codey/transcripts/<session_id>/tool_results/<call_id>-<tool>.txt
    These are written by tool_result_budget when a round's tool messages
    exceed the budget. The original message in history is rewritten to a
    short `<persisted output>` stub pointing at this path.

  - History snapshots: JSON arrays of Message.to_wire() dicts, written
    before llm_compact_history or reactive_compact mutate history. Useful
    for debugging "what was in the prompt right before we summarized."

All writes are best-effort: callers handle exceptions and emit meta lines.
Files are written with mode 0o600 on systems that honor it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal

from ..core.messages import Message

_CACHE_ROOT = Path.home() / ".cache" / "codey"

SnapshotKind = Literal["proactive", "reactive"]

_SAFE_CHAR_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(s: str) -> str:
    """Replace anything not alnum/dot/underscore/dash with '_'. Empty → 'x'."""
    cleaned = _SAFE_CHAR_RE.sub("_", s) if s else ""
    return cleaned or "x"


def persisted_root_for(session_id: str) -> Path:
    return _CACHE_ROOT / "transcripts" / _safe(session_id) / "tool_results"


def snapshots_root_for(session_id: str) -> Path:
    return _CACHE_ROOT / "transcripts" / _safe(session_id) / "snapshots"


def _chmod_quiet(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


def write_persisted_tool_result(
    *, session_id: str, call_id: str, tool_name: str, body: str,
) -> Path:
    """Write a tool result body to disk and return the file path.

    Filename: <safe-call-id>-<safe-tool-name>.txt. Caller is responsible
    for the in-history stub rewrite.
    """
    root = persisted_root_for(session_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_safe(call_id)}-{_safe(tool_name)}.txt"
    path.write_text(body, encoding="utf-8")
    _chmod_quiet(path, 0o600)
    return path


def write_history_snapshot(
    *, session_id: str, history: Iterable[Message], kind: SnapshotKind,
) -> Path:
    """Snapshot `history` as JSON and return the path. Best-effort timestamp."""
    root = snapshots_root_for(session_id)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds").replace(":", "-")
    path = root / f"{stamp}-{kind}.json"
    payload = [m.to_wire() for m in history]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _chmod_quiet(path, 0o600)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_transcripts.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/transcripts.py tests/test_context_transcripts.py
git commit -m "$(cat <<'EOF'
context: disk I/O for persisted tool results and history snapshots

Two writers under ~/.cache/codey/transcripts/<session_id>/:
  - tool_results/<call_id>-<tool>.txt — raw bodies persisted by the
    budget step when a round exceeds 200kb.
  - snapshots/<iso-ts>-{proactive,reactive}.json — pre-compact history
    snapshots for debugging.

Filenames are sanitized so unusual call_ids can't escape the dir.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Step 1 — tool_result_budget (`context/budget.py`)

**Files:**
- Create: `src/codey/context/budget.py`
- Test: `tests/test_context_budget.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_budget.py`:

```python
"""Tests for tool_result_budget — the disk-spill step for big tool outputs."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.context.budget import (
    PERSISTED_STUB_PREFIX,
    TOOL_RESULT_BUDGET_BYTES,
    TOOL_RESULT_PERSIST_PREVIEW_CHARS,
    run as budget_run,
)
from codey.core.messages import Message


def _hist():
    """Pre-built history: system + user + assistant.tool_calls + tool results."""
    return [
        Message(role="system", content="sys"),
        Message(role="user", content="do stuff"),
        Message(role="assistant", content="", tool_calls=[
            {"id": "c1", "type": "function",
             "function": {"name": "bash", "arguments": "{}"}},
            {"id": "c2", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}},
        ]),
        Message(role="tool", tool_call_id="c1", name="bash", content="small"),
        Message(role="tool", tool_call_id="c2", name="read_file", content="also small"),
    ]


def test_no_idxs_is_no_op(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    metas = []
    n = budget_run(history=hist, last_round_tool_idxs=[],
                   session_id="s", meta=metas.append)
    assert n == 0
    assert metas == []


def test_under_threshold_is_no_op(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4],
                   session_id="s", meta=lambda _m: None)
    assert n == 0
    # Bodies unchanged.
    assert hist[3].content == "small"
    assert hist[4].content == "also small"


def test_over_threshold_persists_all_round_tool_results(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    # 180kb + 30kb + 10kb (sum = 220kb > 200kb threshold)
    hist[3].content = "A" * 180_000
    hist[4].content = "B" * 30_000
    hist.append(Message(role="tool", tool_call_id="c3", name="grep", content="C" * 10_000))
    metas = []
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4, 5],
                   session_id="sess", meta=metas.append)
    assert n == 3
    for idx in (3, 4, 5):
        assert hist[idx].content.startswith(PERSISTED_STUB_PREFIX)
        assert "path:" in hist[idx].content
        assert "original_bytes:" in hist[idx].content
    # Files exist on disk.
    bucket = tmp_path / "transcripts" / "sess" / "tool_results"
    assert sorted(p.name for p in bucket.iterdir()) == [
        "c1-bash.txt", "c2-read_file.txt", "c3-grep.txt",
    ]
    assert len(metas) == 1
    assert "persisted 3 tool result" in metas[0]
    assert "kb)" in metas[0]


def test_preview_truncated_to_constant(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    hist[3].content = "X" * 250_000
    hist[4].content = "Y" * 10_000
    budget_run(history=hist, last_round_tool_idxs=[3, 4],
               session_id="s", meta=lambda _m: None)
    stub = hist[3].content
    # The preview slice should be exactly TOOL_RESULT_PERSIST_PREVIEW_CHARS
    # X-characters somewhere inside the stub.
    assert "X" * TOOL_RESULT_PERSIST_PREVIEW_CHARS in stub
    assert "X" * (TOOL_RESULT_PERSIST_PREVIEW_CHARS + 1) not in stub


def test_idempotent_on_second_run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist()
    hist[3].content = "A" * 250_000
    hist[4].content = "B" * 10_000
    budget_run(history=hist, last_round_tool_idxs=[3, 4],
               session_id="s", meta=lambda _m: None)
    first_stub = hist[3].content
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4],
                   session_id="s", meta=lambda _m: None)
    assert n == 0
    assert hist[3].content == first_stub


def test_persist_failure_leaves_message_unchanged(tmp_path: Path, monkeypatch):
    # Force write to fail by pointing the cache at a path where mkdir errors.
    bad = tmp_path / "x"
    bad.write_text("not a dir")          # exists as a file
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", bad)
    hist = _hist()
    hist[3].content = "A" * 250_000
    hist[4].content = "B" * 10_000
    metas = []
    n = budget_run(history=hist, last_round_tool_idxs=[3, 4],
                   session_id="s", meta=metas.append)
    assert n == 0
    assert hist[3].content == "A" * 250_000
    assert any("persist failed" in m for m in metas)


def test_threshold_constant_is_200kb():
    assert TOOL_RESULT_BUDGET_BYTES == 200_000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_budget.py -v`
Expected: ImportError on `codey.context.budget`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/budget.py`:

```python
"""Step 1: tool_result_budget — persist big tool results to disk.

Triggered when the sum of byte-sizes of the last round's tool messages
exceeds 200kb. Each oversized round writes every tool result body to a
file under ~/.cache/codey/transcripts/<session_id>/tool_results/ and
replaces the in-history content with a short `<persisted output>` stub.

Idempotent: messages whose content already starts with PERSISTED_STUB_PREFIX
are skipped.
"""
from __future__ import annotations

from typing import Callable

from ..core.messages import Message
from . import transcripts as _transcripts

TOOL_RESULT_BUDGET_BYTES = 200_000
TOOL_RESULT_PERSIST_PREVIEW_CHARS = 2_000
PERSISTED_STUB_PREFIX = "<persisted output>"

MetaSink = Callable[[str], None]


def _persisted_stub(*, path, original_bytes: int, body: str) -> str:
    preview = body[:TOOL_RESULT_PERSIST_PREVIEW_CHARS]
    return (
        f"{PERSISTED_STUB_PREFIX}\n"
        f"path: {path}\n"
        f"original_bytes: {original_bytes}\n"
        f"preview (first {len(preview)} of {len(body)} chars):\n"
        f"{preview}"
    )


def run(
    *,
    history: list[Message],
    last_round_tool_idxs: list[int],
    session_id: str,
    meta: MetaSink | None,
) -> int:
    """Persist last-round tool results past the budget. Returns # persisted."""
    if not last_round_tool_idxs:
        return 0

    # Filter to indices that are valid in-bounds tool messages and not
    # already persisted from a previous run.
    candidates: list[tuple[int, int]] = []
    for i in last_round_tool_idxs:
        if not (0 <= i < len(history)):
            continue
        msg = history[i]
        if msg.role != "tool" or not msg.content:
            continue
        if msg.content.startswith(PERSISTED_STUB_PREFIX):
            continue
        candidates.append((i, len(msg.content.encode("utf-8"))))

    total = sum(sz for _, sz in candidates)
    if total <= TOOL_RESULT_BUDGET_BYTES:
        return 0

    persisted = 0
    total_bytes_persisted = 0
    for idx, sz in candidates:
        msg = history[idx]
        try:
            path = _transcripts.write_persisted_tool_result(
                session_id=session_id,
                call_id=msg.tool_call_id or "unknown",
                tool_name=msg.name or "tool",
                body=msg.content,
            )
        except Exception as e:  # noqa: BLE001
            if meta:
                meta(f"[ctx: persist failed for {msg.tool_call_id}: "
                     f"{type(e).__name__}: {e}]")
            continue
        msg.content = _persisted_stub(path=path, original_bytes=sz, body=msg.content)
        persisted += 1
        total_bytes_persisted += sz

    if meta and persisted:
        meta(f"[ctx: persisted {persisted} tool result"
             f"{'s' if persisted > 1 else ''} "
             f"({total_bytes_persisted // 1000}kb) to disk]")
    return persisted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_budget.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/budget.py tests/test_context_budget.py
git commit -m "$(cat <<'EOF'
context: tool_result_budget — spill big tool outputs to disk

Step 1 of the proactive pipeline. When the sum of the last round's
tool-result byte sizes exceeds 200kb, write each body to
~/.cache/codey/transcripts/<sid>/tool_results/<call_id>-<tool>.txt and
replace the in-history content with a <persisted output> stub
containing the path, original size, and a 2000-char preview.

Idempotent: stubs detect themselves on a second pass.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Step 2 — snip_compact (`context/snip.py`)

**Files:**
- Create: `src/codey/context/snip.py`
- Test: `tests/test_context_snip.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_snip.py`:

```python
"""Tests for snip_compact and the pair-boundary helpers."""
from __future__ import annotations

from codey.context.snip import (
    SNIP_KEEP_HEAD,
    SNIP_KEEP_TAIL,
    SNIP_THRESHOLD_MESSAGES,
    _expand_prefix_to_pair_boundary,
    _expand_suffix_to_pair_boundary,
    run as snip_run,
)
from codey.core.messages import Message


# -- helpers --

def _user(i: int) -> Message:
    return Message(role="user", content=f"u{i}")

def _asst(i: int) -> Message:
    return Message(role="assistant", content=f"a{i}")

def _call(call_id: str, fn: str = "bash") -> Message:
    return Message(role="assistant", content="", tool_calls=[
        {"id": call_id, "type": "function",
         "function": {"name": fn, "arguments": "{}"}},
    ])

def _result(call_id: str, fn: str = "bash") -> Message:
    return Message(role="tool", tool_call_id=call_id, name=fn, content=f"r{call_id}")


# -- threshold / no-op cases --

def test_under_threshold_is_no_op():
    body = [_user(i) for i in range(SNIP_THRESHOLD_MESSAGES)]
    hist = [Message(role="system", content="s")] + body
    metas = []
    n = snip_run(history=hist, meta=metas.append)
    assert n == 0
    assert metas == []
    assert len(hist) == SNIP_THRESHOLD_MESSAGES + 1


def test_just_over_threshold_snips_to_head_plus_marker_plus_tail():
    body = [_user(i) for i in range(SNIP_THRESHOLD_MESSAGES + 5)]
    hist = [Message(role="system", content="s")] + body
    metas = []
    n = snip_run(history=hist, meta=metas.append)
    # We had THRESHOLD+5 = 55. Keep 5 head + 45 tail = 50. Drop 5 → marker.
    assert n == 5
    # Expected layout: system + head(5) + marker(1) + tail(45) = 52
    assert len(hist) == 1 + SNIP_KEEP_HEAD + 1 + SNIP_KEEP_TAIL
    # The marker is at sys_count + SNIP_KEEP_HEAD.
    marker = hist[1 + SNIP_KEEP_HEAD]
    assert marker.role == "user"
    assert marker.content.startswith("[... 5 earlier message")
    assert "compacted by snip" in marker.content
    assert metas == ["[ctx: snipped 5 middle messages]"]


# -- pair-boundary expansion --

def test_expand_prefix_pulls_in_matching_tool_results():
    body = [
        _user(0), _user(1), _user(2), _user(3),
        _call("c1"),         # idx 4 — has tool_calls
        _result("c1"),       # idx 5 — matching result
        _user(6),
    ]
    # Proposed end = 5 (would slice body[:5]), but idx 4 has tool_calls and
    # the matching result is at idx 5 → expand to 6.
    new_end = _expand_prefix_to_pair_boundary(body, 5)
    assert new_end == 6


def test_expand_prefix_no_op_when_no_pending_pair():
    body = [_user(0), _user(1), _user(2)]
    assert _expand_prefix_to_pair_boundary(body, 2) == 2


def test_expand_suffix_pulls_in_originating_assistant():
    body = [
        _user(0),
        _call("c1"),        # idx 1 — assistant with tool_calls
        _result("c1"),      # idx 2 — tool result
        _user(3),
    ]
    # Proposed suffix start = 2 (a tool message). Should walk back to 1.
    assert _expand_suffix_to_pair_boundary(body, 2) == 1


def test_expand_suffix_no_op_when_not_a_tool_message():
    body = [_user(0), _user(1), _user(2)]
    assert _expand_suffix_to_pair_boundary(body, 2) == 2


# -- snip uses the expanders --

def test_snip_left_boundary_expands_when_inside_pair():
    # Construct: system + 4 users + call/result/call/result + 50 users
    # Head=5 would cut at body idx 5 (mid-pair: call at 4, result at 5).
    head_block = [_user(0), _user(1), _user(2), _user(3), _call("c1"), _result("c1")]
    middle    = [_user(i) for i in range(10, 20)]    # 10 middle messages we want gone
    tail      = [_user(i) for i in range(100, 145)]  # 45 tail messages
    body = head_block + middle + tail
    hist = [Message(role="system", content="s")] + body
    n = snip_run(history=hist, meta=lambda _m: None)
    # The 10 middle messages should be dropped. We keep 4 users + call + result
    # on the left (because the expander pulled in idx 5).
    # Layout: system + 6 kept + marker + 45 = 53
    assert n == 10
    assert len(hist) == 1 + 6 + 1 + 45
    # The kept prefix's last message is the tool result.
    assert hist[6].role == "tool"


def test_snip_right_boundary_expands_when_inside_pair():
    # Construct so the right boundary lands on a tool result.
    head = [_user(i) for i in range(5)]
    middle = [_user(i) for i in range(100, 110)]    # 10 to drop
    # Tail = call/result + 43 users = 45 messages, but we'll bump suffix
    # start so its first message is the result.
    tail = [_call("ct"), _result("ct")] + [_user(i) for i in range(200, 243)]
    body = head + middle + tail
    hist = [Message(role="system", content="s")] + body
    # Body length = 5 + 10 + 45 = 60. THRESHOLD=50 → snip fires.
    # Tail keep = 45 → suffix start in body = 60-45 = 15, which is the
    # tool result. Expander pulls it back to 14 (the call). So we drop
    # 9 instead of 10.
    n = snip_run(history=hist, meta=lambda _m: None)
    assert n == 9
    # Final layout: system + 5 + marker + 46 = 53
    assert len(hist) == 1 + 5 + 1 + 46
    assert hist[7].role == "assistant" and hist[7].tool_calls


def test_snip_no_op_when_windows_overlap_after_expansion():
    # Pathological: body length is barely over threshold, expansion makes
    # head and tail meet. snip should bail rather than produce a broken
    # history.
    # Head=5, Tail=45. Body length = 50 → no-op anyway.
    # Force length 51 with expansion that swallows the boundary.
    body = [_user(i) for i in range(4)] + [_call("c"), _result("c")] + \
           [_user(i) for i in range(100, 145)]
    # body length = 4 + 2 + 45 = 51. Drop 1 unless windows touch.
    hist = [Message(role="system", content="s")] + body
    n = snip_run(history=hist, meta=lambda _m: None)
    # snip kicks in because 51 > 50. Head-end expander leaves prefix at 6
    # (4 users + call + result). Tail-start = 51 - 45 = 6 too. dropped = 0.
    # So the function should return 0 and emit no meta.
    assert n == 0
    assert len(hist) == 52
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_snip.py -v`
Expected: ImportError on `codey.context.snip`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/snip.py`:

```python
"""Step 2: snip_compact — trim the middle of long conversations.

When the non-system history exceeds 50 messages, keep the first 5 and last
45 and drop everything between. Replace the dropped block with a single
synthetic user marker so the model is aware of the gap.

Pair preservation: if either cut boundary falls inside a tool_call ↔
tool_result group, expand the keep-window outward so no orphan messages
survive. The two expander helpers are also re-used by reactive.py.
"""
from __future__ import annotations

from typing import Callable

from ..core.messages import Message

SNIP_THRESHOLD_MESSAGES = 50
SNIP_KEEP_HEAD = 5
SNIP_KEEP_TAIL = 45

MetaSink = Callable[[str], None]


def _expand_prefix_to_pair_boundary(body: list[Message], end_idx: int) -> int:
    """If body[end_idx-1] is an assistant.tool_calls, walk forward absorbing
    matching role:"tool" messages. Returns the new exclusive end index."""
    if end_idx <= 0 or end_idx > len(body):
        return end_idx
    last = body[end_idx - 1]
    if last.role != "assistant" or not last.tool_calls:
        return end_idx
    expected = {c["id"] for c in last.tool_calls if c.get("id")}
    if not expected:
        return end_idx
    i = end_idx
    seen: set[str] = set()
    while i < len(body) and body[i].role == "tool":
        if body[i].tool_call_id:
            seen.add(body[i].tool_call_id)
        i += 1
        if expected.issubset(seen):
            break
    return i


def _expand_suffix_to_pair_boundary(body: list[Message], start_idx: int) -> int:
    """If body[start_idx] is role:"tool", walk backward to include the
    originating assistant.tool_calls message + any sibling tool results
    that share its call group. Returns the new (possibly smaller) start."""
    if start_idx < 0 or start_idx >= len(body):
        return start_idx
    if body[start_idx].role != "tool":
        return start_idx
    i = start_idx
    while i > 0 and body[i - 1].role == "tool":
        i -= 1
    # i is now the first tool message in the contiguous block. Look at
    # body[i-1] for the originating assistant.tool_calls.
    if i - 1 >= 0:
        prev = body[i - 1]
        if prev.role == "assistant" and prev.tool_calls:
            return i - 1
    return i


def run(*, history: list[Message], meta: MetaSink | None) -> int:
    """Trim the middle of history. Returns the number of messages dropped."""
    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    if len(body) <= SNIP_THRESHOLD_MESSAGES:
        return 0

    prefix_end = _expand_prefix_to_pair_boundary(body, SNIP_KEEP_HEAD)
    suffix_start = _expand_suffix_to_pair_boundary(
        body, len(body) - SNIP_KEEP_TAIL
    )
    if suffix_start <= prefix_end:
        return 0

    dropped = suffix_start - prefix_end
    if dropped <= 0:
        return 0

    marker = Message(
        role="user",
        content=f"[... {dropped} earlier message"
                f"{'s' if dropped > 1 else ''} compacted by snip ...]",
    )
    new_body = body[:prefix_end] + [marker] + body[suffix_start:]
    history[sys_count:] = new_body

    if meta:
        meta(f"[ctx: snipped {dropped} middle messages]")
    return dropped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_snip.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/snip.py tests/test_context_snip.py
git commit -m "$(cat <<'EOF'
context: snip_compact — middle-trim past 50 messages, pair-aware

Step 2 of the proactive pipeline. Keeps first 5 + last 45 non-system
messages; drops the middle and inserts a single user-role gap marker.

If the cut boundary falls inside a tool_call ↔ tool_result group, the
boundary expands outward so no orphan tool messages survive. Helpers
are reused by reactive_compact.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Step 3 — micro_compact (`context/micro.py`)

**Files:**
- Create: `src/codey/context/micro.py`
- Test: `tests/test_context_micro.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_micro.py`:

```python
"""Tests for micro_compact — placeholder all but the last 5 tool results."""
from __future__ import annotations

from codey.context.micro import (
    MICRO_KEEP_RECENT_TOOL_RESULTS,
    PLACEHOLDER,
    run as micro_run,
)
from codey.core.messages import Message


def _tool(call_id: str, content: str = "x") -> Message:
    return Message(role="tool", tool_call_id=call_id, name="t", content=content)


def test_under_threshold_is_no_op():
    hist = [Message(role="system", content="s")] + [_tool(f"c{i}", "x") for i in range(5)]
    n = micro_run(history=hist, meta=lambda _m: None)
    assert n == 0
    for m in hist[1:]:
        assert m.content == "x"


def test_over_threshold_replaces_all_but_last_5():
    tools = [_tool(f"c{i}", f"body{i}") for i in range(10)]
    hist = [Message(role="system", content="s")] + tools
    metas = []
    n = micro_run(history=hist, meta=metas.append)
    assert n == 5    # first 5 of 10 get placeholdered
    for i in range(5):
        assert hist[1 + i].content == PLACEHOLDER
    for i in range(5, 10):
        assert hist[1 + i].content == f"body{i}"
    assert metas == ["[ctx: replaced 5 old tool results with placeholder]"]


def test_idempotent_second_run():
    tools = [_tool(f"c{i}", f"body{i}") for i in range(8)]
    hist = [Message(role="system", content="s")] + tools
    micro_run(history=hist, meta=lambda _m: None)
    metas = []
    n = micro_run(history=hist, meta=metas.append)
    assert n == 0
    assert metas == []


def test_interleaved_messages_are_skipped():
    hist = [
        Message(role="system", content="s"),
        Message(role="user", content="u0"),
        _tool("c0", "body0"),
        Message(role="assistant", content="a0"),
        _tool("c1", "body1"),
        _tool("c2", "body2"),
        Message(role="user", content="u1"),
        _tool("c3", "body3"),
        _tool("c4", "body4"),
        _tool("c5", "body5"),
        _tool("c6", "body6"),
        _tool("c7", "body7"),    # 8 tool messages total, drop oldest 3
    ]
    n = micro_run(history=hist, meta=lambda _m: None)
    assert n == 3
    # Tool indices are 2, 4, 5, 7, 8, 9, 10, 11 — oldest 3 = 2, 4, 5.
    assert hist[2].content == PLACEHOLDER
    assert hist[4].content == PLACEHOLDER
    assert hist[5].content == PLACEHOLDER
    # Most-recent 5 untouched.
    assert hist[7].content == "body3"
    assert hist[11].content == "body7"


def test_keep_constant_is_five():
    assert MICRO_KEEP_RECENT_TOOL_RESULTS == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_micro.py -v`
Expected: ImportError on `codey.context.micro`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/micro.py`:

```python
"""Step 3: micro_compact — placeholder old tool result bodies.

Walks history and replaces all but the last 5 tool-result bodies with a
fixed placeholder. Runs unconditionally — no threshold — because the cost
of the walk is negligible and the operation is idempotent once everything
old is already a placeholder.
"""
from __future__ import annotations

from typing import Callable

from ..core.messages import Message

MICRO_KEEP_RECENT_TOOL_RESULTS = 5
PLACEHOLDER = "[Earlier tool result compacted. Re-run if needed.]"

MetaSink = Callable[[str], None]


def run(*, history: list[Message], meta: MetaSink | None) -> int:
    """Replace old tool-result bodies with the placeholder. Returns # replaced."""
    tool_idxs = [i for i, m in enumerate(history) if m.role == "tool"]
    if len(tool_idxs) <= MICRO_KEEP_RECENT_TOOL_RESULTS:
        return 0
    cutoff = len(tool_idxs) - MICRO_KEEP_RECENT_TOOL_RESULTS
    replaced = 0
    for i in tool_idxs[:cutoff]:
        if history[i].content != PLACEHOLDER:
            history[i].content = PLACEHOLDER
            replaced += 1
    if meta and replaced:
        meta(f"[ctx: replaced {replaced} old tool result"
             f"{'s' if replaced > 1 else ''} with placeholder]")
    return replaced
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_micro.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/micro.py tests/test_context_micro.py
git commit -m "$(cat <<'EOF'
context: micro_compact — keep last 5 tool result bodies

Step 3 of the proactive pipeline. Replaces all but the 5 most recent
tool-result bodies with a fixed placeholder telling the model to re-run
if it needs the data. Runs unconditionally; silent on no-op.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — LLM step, reactive path, orchestrator, profile fields

This phase adds the parts that touch the model client and config. Still no wiring into `turn.py`.

### Task 8: Profile fields — context_window, max_output_tokens, compact_headroom

**Files:**
- Modify: `src/codey/config.py`
- Test: `tests/test_profile_context_fields.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_profile_context_fields.py`:

```python
"""Tests for the new Profile context-management fields."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.config import ConfigFile, DEFAULT_COMPACT_HEADROOM, DEFAULT_CONTEXT_WINDOW, DEFAULT_MAX_OUTPUT_TOKENS


def test_defaults_when_unset(temp_config: Path):
    cfg = ConfigFile.load()
    p = cfg.resolve("alpha")
    assert p.context_window == DEFAULT_CONTEXT_WINDOW
    assert p.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert p.compact_headroom == DEFAULT_COMPACT_HEADROOM


def test_defaults_are_documented_values():
    assert DEFAULT_CONTEXT_WINDOW == 1_000_000
    assert DEFAULT_MAX_OUTPUT_TOKENS == 4_096
    assert DEFAULT_COMPACT_HEADROOM == 13_000


def test_overrides_in_config(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'default_profile = "small"\n'
        "\n"
        "[profiles.small]\n"
        'base_url = "https://example/v1"\n'
        'api_key  = "k"\n'
        'model    = "tiny"\n'
        "context_window    = 32000\n"
        "max_output_tokens = 1024\n"
        "compact_headroom  = 2000\n"
    )
    monkeypatch.setattr("codey.config.CONFIG_PATH", cfg_path)
    for k in ("CODEY_API_KEY", "CODEY_BASE_URL", "CODEY_MODEL", "CODEY_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    p = ConfigFile.load().resolve("small")
    assert p.context_window == 32000
    assert p.max_output_tokens == 1024
    assert p.compact_headroom == 2000


def test_partial_override(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'default_profile = "p"\n'
        "\n"
        "[profiles.p]\n"
        'base_url = "x"\n'
        'api_key  = "k"\n'
        'model    = "m"\n'
        "context_window = 16000\n"   # only one field overridden
    )
    monkeypatch.setattr("codey.config.CONFIG_PATH", cfg_path)
    for k in ("CODEY_API_KEY", "CODEY_BASE_URL", "CODEY_MODEL", "CODEY_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    p = ConfigFile.load().resolve("p")
    assert p.context_window == 16000
    assert p.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS
    assert p.compact_headroom == DEFAULT_COMPACT_HEADROOM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_profile_context_fields.py -v`
Expected: ImportError on `DEFAULT_CONTEXT_WINDOW` (the constants don't exist yet).

- [ ] **Step 3: Edit `src/codey/config.py`**

Add these constants near the existing `DEFAULT_BASE_URL` / `DEFAULT_MODEL`:

```python
DEFAULT_CONTEXT_WINDOW = 1_000_000      # 1M tokens — mainstream long-context tier
DEFAULT_MAX_OUTPUT_TOKENS = 4_096
DEFAULT_COMPACT_HEADROOM = 13_000
```

Add three fields to the `Profile` dataclass (after the existing `model` field):

```python
@dataclass(frozen=True)
class Profile:
    name: str
    api_key: str
    base_url: str
    model: str
    context_window: int = DEFAULT_CONTEXT_WINDOW
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    compact_headroom: int = DEFAULT_COMPACT_HEADROOM
```

In `ConfigFile.load()`, inside the `for name, body in raw_profiles.items():` loop, extend the `Profile(...)` construction to read the optional keys:

```python
profiles[name] = Profile(
    name=name,
    api_key=body.get("api_key", ""),
    base_url=body.get("base_url", DEFAULT_BASE_URL),
    model=body.get("model", DEFAULT_MODEL),
    context_window=int(body.get("context_window", DEFAULT_CONTEXT_WINDOW)),
    max_output_tokens=int(body.get("max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)),
    compact_headroom=int(body.get("compact_headroom", DEFAULT_COMPACT_HEADROOM)),
)
```

Also adjust the `replace(...)` call in `ConfigFile.resolve(...)` only if needed (no behavior change for the new fields — `replace` preserves them).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_profile_context_fields.py tests/test_skills.py -v`
Expected: new tests pass; existing skills tests still pass.

- [ ] **Step 5: Run the full suite to check nothing regressed**

Run: `uv run pytest`
Expected: all green (count = previous + 4).

- [ ] **Step 6: Commit**

```bash
git add src/codey/config.py tests/test_profile_context_fields.py
git commit -m "$(cat <<'EOF'
config: Profile gains context_window / max_output_tokens / compact_headroom

Three optional [profiles.*] fields with defaults sized for today's
mainstream long-context tier (1M / 4k / 13k). Used by the upcoming
context-management pipeline to decide when to summarize history.

Existing config.toml files load unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Step 4 — llm_compact_history (`context/llm.py`)

**Files:**
- Create: `src/codey/context/llm.py`
- Test: `tests/test_context_llm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_llm.py`:

```python
"""Tests for llm_compact_history."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from codey.config import Profile
from codey.context import llm as llm_mod
from codey.core.messages import Message


class FakeStreamChunk:
    def __init__(self, content: str = "", tool_calls=None):
        self.choices = [type("C", (), {
            "delta": type("D", (), {"content": content, "tool_calls": tool_calls})()
        })()]


class FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class FakeChatCompletions:
    def __init__(self, *, response_text: str):
        self.response_text = response_text
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return FakeAsyncStream([FakeStreamChunk(self.response_text)])
        return type("R", (), {
            "choices": [type("C", (), {
                "message": type("M", (), {"content": self.response_text})()
            })()]
        })()


class FakeClient:
    def __init__(self, *, response_text: str):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeChatCompletions(response_text=response_text)


def _profile() -> Profile:
    return Profile(name="p", api_key="k", base_url="https://x",
                   model="m", context_window=100_000,
                   max_output_tokens=4_096, compact_headroom=13_000)


@pytest.mark.asyncio
async def test_compact_replaces_history_with_system_plus_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    history = [
        Message(role="system", content="sys prompt"),
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi"),
        Message(role="user", content="big task"),
    ]
    client = FakeClient(response_text="user wants X; we're doing Y")
    metas = []
    ok = await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=metas.append, client=client, recent_files=[],
    )
    assert ok is True
    assert len(history) == 2
    assert history[0].role == "system"
    assert history[0].content == "sys prompt"
    assert history[1].role == "user"
    assert "Summary of prior conversation" in history[1].content
    assert "user wants X; we're doing Y" in history[1].content
    assert "Snapshot:" in history[1].content
    assert "Conversation compacted at" in history[1].content
    assert metas and "summarized history" in metas[0]


@pytest.mark.asyncio
async def test_compact_reads_recent_files(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    f1 = tmp_path / "a.txt"; f1.write_text("alpha contents")
    f2 = tmp_path / "b.txt"; f2.write_text("beta contents")
    history = [Message(role="system", content="s"),
               Message(role="user", content="hi")]
    client = FakeClient(response_text="summary")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=[f1, f2],
    )
    body = history[1].content
    assert "alpha contents" in body
    assert "beta contents" in body
    assert f"--- {f1} ---" in body
    assert f"--- {f2} ---" in body


@pytest.mark.asyncio
async def test_compact_handles_missing_recent_file(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    missing = tmp_path / "never_existed.txt"
    history = [Message(role="user", content="hi")]
    client = FakeClient(response_text="summary")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=[missing],
    )
    assert "(error:" in history[0].content


@pytest.mark.asyncio
async def test_compact_writes_snapshot_file(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    history = [Message(role="system", content="s"),
               Message(role="user", content="u")]
    client = FakeClient(response_text="ok")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=[],
    )
    snaps = list((tmp_path / "transcripts" / "sid" / "snapshots").iterdir())
    assert len(snaps) == 1
    assert "proactive" in snaps[0].name
    data = json.loads(snaps[0].read_text())
    assert any(m.get("content") == "u" for m in data)


@pytest.mark.asyncio
async def test_compact_caps_at_max_files(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    files = []
    for i in range(8):
        p = tmp_path / f"f{i}.txt"
        p.write_text(f"contents-{i}")
        files.append(p)
    history = [Message(role="user", content="hi")]
    client = FakeClient(response_text="summary")
    await llm_mod.run(
        history=history, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=client, recent_files=files,
    )
    body = history[0].content
    # The 5 most recent (last in the list) should appear.
    for i in range(3, 8):
        assert f"contents-{i}" in body
    # The earlier ones should NOT appear.
    for i in range(0, 3):
        assert f"contents-{i}" not in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_llm.py -v`
Expected: ImportError on `codey.context.llm`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/llm.py`:

```python
"""Step 4: llm_compact_history — single API call summary.

Replaces the conversation body with a synthetic user message containing
the model's own summary of the prior history plus a re-read of the most
recent 5 files the agent looked at. System messages survive untouched.

Triggered by the orchestrator when estimate(history) >
profile.context_window - profile.max_output_tokens - profile.compact_headroom.

Same client + profile as the active agent. The summary call is non-
streaming, tool-less, low-temperature.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from openai import AsyncOpenAI

from ..config import Profile
from ..core.messages import Message
from . import transcripts as _transcripts

MAX_RECENT_FILES = 5

_SUMMARY_SYSTEM_PROMPT = (
    "You are a context-compaction assistant. Given the conversation "
    "history below, produce a concise summary suitable for resuming the "
    "conversation. Cover:\n"
    "1. What the user is trying to accomplish (the overarching goal).\n"
    "2. Key decisions made, constraints identified, and architecture choices.\n"
    "3. Files / paths / functions touched or referenced, with one-line "
    "purpose each.\n"
    "4. The current state of work — what's done, what's in progress, what's "
    "blocked, and on what.\n"
    "5. Anything the user explicitly asked the assistant to remember or avoid.\n\n"
    "Be specific. Preserve names, paths, and numbers. Do not invent details "
    "that aren't in the history. Output plain prose; no headings, no JSON. "
    "Aim for 400-800 words."
)

MetaSink = Callable[[str], None]


def _render_transcript(history: Iterable[Message]) -> str:
    """Flatten history into a single text blob the summarizer can read."""
    lines: list[str] = []
    for m in history:
        if m.role == "system":
            lines.append(f"[system]\n{m.content}\n")
            continue
        if m.role == "tool":
            if m.content.startswith("<persisted output>") or "compacted" in m.content:
                lines.append(f"[tool:{m.name or '?'}] (earlier tool output omitted)")
            else:
                lines.append(f"[tool:{m.name or '?'}]\n{m.content}\n")
            continue
        if m.role == "assistant" and m.tool_calls:
            calls = ", ".join(
                f"{c.get('function', {}).get('name', '?')}({c.get('function', {}).get('arguments', '')})"
                for c in m.tool_calls
            )
            txt = m.content or ""
            lines.append(f"[assistant tool_calls]\n{txt}\n→ {calls}\n")
            continue
        lines.append(f"[{m.role}]\n{m.content}\n")
    return "\n".join(lines)


async def _summarize(client: AsyncOpenAI, profile: Profile, history: list[Message]) -> str:
    transcript = _render_transcript(history)
    resp = await client.chat.completions.create(
        model=profile.model,
        stream=False,
        temperature=0.2,
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    )
    return resp.choices[0].message.content or ""


def _read_recent_files(recent_files: Iterable[Path], max_files: int) -> list[tuple[Path, str]]:
    """Read up to `max_files` of the most recent paths. Returns (path, body_or_error)."""
    files = list(recent_files)[-max_files:]
    out: list[tuple[Path, str]] = []
    for p in files:
        try:
            out.append((p, Path(p).read_text(encoding="utf-8", errors="replace")))
        except Exception as e:  # noqa: BLE001
            out.append((p, f"(error: {type(e).__name__}: {e})"))
    return out


def _build_replacement_user_message(
    *, summary: str, file_blocks: list[tuple[Path, str]],
    snapshot_path: Path | None, header: str | None = None,
) -> str:
    stamp = datetime.now().isoformat(timespec="seconds")
    parts: list[str] = []
    if header:
        parts.append(header)
    snap = f" Snapshot: {snapshot_path}." if snapshot_path else ""
    parts.append(f"[Conversation compacted at {stamp}.{snap}]")
    parts.append("")
    parts.append("Summary of prior conversation:")
    parts.append(summary.strip() or "(empty summary)")
    if file_blocks:
        parts.append("")
        parts.append("Recent files (re-read at compact time):")
        for path, body in file_blocks:
            parts.append("")
            parts.append(f"--- {path} ---")
            parts.append(body.rstrip())
    return "\n".join(parts)


async def run(
    *,
    history: list[Message],
    profile: Profile,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
) -> bool:
    """Summarize history into a single user message. Returns True on success."""
    try:
        snapshot_path = _transcripts.write_history_snapshot(
            session_id=session_id, history=history, kind="proactive",
        )
    except Exception:  # noqa: BLE001
        snapshot_path = None

    summary = await _summarize(client, profile, history)
    file_blocks = _read_recent_files(recent_files, MAX_RECENT_FILES)

    system_msgs = [m for m in history if m.role == "system"]
    body = _build_replacement_user_message(
        summary=summary, file_blocks=file_blocks, snapshot_path=snapshot_path,
    )
    history[:] = system_msgs + [Message(role="user", content=body)]

    if meta:
        snap = f" (snapshot: {snapshot_path})" if snapshot_path else ""
        meta(f"[ctx: summarized history → 1 message{snap}]")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_llm.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/llm.py tests/test_context_llm.py
git commit -m "$(cat <<'EOF'
context: llm_compact_history — single-call summary + recent-file re-read

Step 4 of the proactive pipeline. When the chars/4 estimate exceeds the
headroom-adjusted threshold, this step:
  1. snapshots history to disk under transcripts/<sid>/snapshots/
  2. asks the same model to produce a 400-800 word summary
  3. re-reads the 5 most-recent files the agent looked at
  4. replaces history with [system...] + one synthetic user message

Same client+profile as the active agent — no second config knob.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Failure path — reactive_compact (`context/reactive.py`)

**Files:**
- Create: `src/codey/context/reactive.py`
- Test: `tests/test_context_reactive.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_reactive.py`:

```python
"""Tests for reactive_compact — the post-413 recovery path."""
from __future__ import annotations

import pytest

from codey.config import Profile
from codey.context import reactive as reactive_mod
from codey.core.messages import Message

# Reuse the fake client from test_context_llm.
from tests.test_context_llm import FakeClient


def _profile():
    return Profile(name="p", api_key="k", base_url="x", model="m",
                   context_window=100_000, max_output_tokens=4_096,
                   compact_headroom=13_000)


def _hist(n_body: int):
    h = [Message(role="system", content="sys")]
    for i in range(n_body):
        h.append(Message(role="user", content=f"u{i}"))
    return h


@pytest.mark.asyncio
async def test_reactive_keeps_system_summary_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist(20)
    client = FakeClient(response_text="summary text")
    metas = []
    await reactive_mod.run(
        history=hist, profile=_profile(), session_id="sid",
        meta=metas.append, client=client, recent_files=[],
    )
    # Layout: system(1) + summary user(1) + tail(REACTIVE_TAIL)
    assert hist[0].role == "system"
    assert hist[1].role == "user"
    assert "Reactive compact triggered" in hist[1].content
    assert "summary text" in hist[1].content
    tail = hist[2:]
    assert len(tail) == reactive_mod.REACTIVE_TAIL
    # Last tail message should be the last original.
    assert tail[-1].content == "u19"
    assert any("reactive compact triggered" in m for m in metas)


@pytest.mark.asyncio
async def test_reactive_expands_tail_to_pair_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    body = [Message(role="user", content=f"u{i}") for i in range(10)]
    # Insert a tool_call ↔ tool_result group so the tail boundary lands
    # on the tool result.
    body.append(Message(role="assistant", content="", tool_calls=[
        {"id": "c1", "type": "function",
         "function": {"name": "t", "arguments": "{}"}},
    ]))
    body.append(Message(role="tool", tool_call_id="c1", name="t", content="r"))
    body.extend(Message(role="user", content=f"v{i}") for i in range(3))
    # body length = 10 + 2 + 3 = 15; REACTIVE_TAIL=5 → start=10 (the call).
    # Expander already at the assistant, so no movement.
    hist = [Message(role="system", content="s")] + body
    await reactive_mod.run(
        history=hist, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=FakeClient(response_text="ok"),
        recent_files=[],
    )
    tail = hist[2:]
    # The tail must start with a valid pair (no orphan tool result).
    if any(m.role == "tool" for m in tail):
        # Find the first tool message; the message before it must be its
        # originating assistant.tool_calls.
        for i, m in enumerate(tail):
            if m.role == "tool":
                assert i > 0
                assert tail[i - 1].role == "assistant"
                assert tail[i - 1].tool_calls
                break


@pytest.mark.asyncio
async def test_reactive_writes_reactive_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = _hist(10)
    await reactive_mod.run(
        history=hist, profile=_profile(), session_id="sid",
        meta=lambda _m: None, client=FakeClient(response_text="s"),
        recent_files=[],
    )
    snaps = list((tmp_path / "transcripts" / "sid" / "snapshots").iterdir())
    assert len(snaps) == 1
    assert "reactive" in snaps[0].name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_reactive.py -v`
Expected: ImportError on `codey.context.reactive`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/reactive.py`:

```python
"""Reactive compaction — runs on PromptTooLongError.

Same shape as llm_compact_history but more aggressive: keep system +
summary intro + only the last REACTIVE_TAIL messages (expanded outward
for tool-pair safety). The orchestrator caps this to 1 retry per turn;
a second failure surfaces the original error.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from openai import AsyncOpenAI

from ..config import Profile
from ..core.messages import Message
from . import llm as _llm
from . import snip as _snip
from . import transcripts as _transcripts

REACTIVE_TAIL = 5
REACTIVE_MAX_RETRIES = 1

MetaSink = Callable[[str], None]


async def run(
    *,
    history: list[Message],
    profile: Profile,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
) -> None:
    try:
        snapshot_path = _transcripts.write_history_snapshot(
            session_id=session_id, history=history, kind="reactive",
        )
    except Exception:  # noqa: BLE001
        snapshot_path = None

    summary = await _llm._summarize(client, profile, history)
    file_blocks = _llm._read_recent_files(recent_files, _llm.MAX_RECENT_FILES)

    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    tail_start = max(0, len(body) - REACTIVE_TAIL)
    tail_start = _snip._expand_suffix_to_pair_boundary(body, tail_start)
    tail = body[tail_start:]

    intro = Message(
        role="user",
        content=_llm._build_replacement_user_message(
            summary=summary, file_blocks=file_blocks,
            snapshot_path=snapshot_path,
            header="[Reactive compact triggered after PromptTooLong]",
        ),
    )
    history[:] = list(history[:sys_count]) + [intro] + list(tail)

    if meta:
        meta(f"[ctx: reactive compact triggered (kept last {len(tail)} msgs)]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_reactive.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/context/reactive.py tests/test_context_reactive.py
git commit -m "$(cat <<'EOF'
context: reactive_compact — post-413 aggressive summary + tail

Shares the summarize / file-read / replacement-message helpers with
llm_compact. Differs by keeping only the last REACTIVE_TAIL messages
(default 5), expanded outward via snip._expand_suffix_to_pair_boundary
so no orphan tool messages survive. Writes a 'reactive' snapshot for
debugging. The orchestrator caps retries at 1 per turn.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Orchestrator (`context/pipeline.py`)

**Files:**
- Create: `src/codey/context/pipeline.py`
- Modify: `src/codey/context/__init__.py` (re-exports)
- Test: `tests/test_context_pipeline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_context_pipeline.py`:

```python
"""Tests for the orchestrator that chains the 4 steps."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.config import Profile
from codey.context import pipeline as pipeline_mod
from codey.context.pipeline import PARENT_THRESHOLDS, CHILD_THRESHOLDS, Thresholds
from codey.core.messages import Message

from tests.test_context_llm import FakeClient


def _profile(window=100_000, headroom=13_000) -> Profile:
    return Profile(name="p", api_key="k", base_url="x", model="m",
                   context_window=window, max_output_tokens=4_096,
                   compact_headroom=headroom)


@pytest.mark.asyncio
async def test_run_proactive_small_history_is_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = [Message(role="system", content="s"),
            Message(role="user", content="hi")]
    metas = []
    await pipeline_mod.run_proactive(
        history=hist, profile=_profile(), session_id="sid",
        last_round_tool_idxs=[], meta=metas.append,
        client=FakeClient(response_text="x"), recent_files=[],
    )
    assert metas == []
    assert len(hist) == 2


@pytest.mark.asyncio
async def test_run_proactive_runs_budget_then_snip_then_micro(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    # Build a history that triggers budget AND snip AND micro.
    hist = [Message(role="system", content="s")]
    # 50 user messages, plus 10 tool results to trigger micro.
    for i in range(50):
        hist.append(Message(role="user", content=f"u{i}"))
    for i in range(10):
        hist.append(Message(role="tool", tool_call_id=f"c{i}", name="t",
                            content=f"body{i}"))
    # Last round: two huge tool messages.
    hist[-2].content = "X" * 180_000
    hist[-1].content = "Y" * 30_000
    last_round_idxs = [len(hist) - 2, len(hist) - 1]

    metas = []
    await pipeline_mod.run_proactive(
        history=hist, profile=_profile(), session_id="sid",
        last_round_tool_idxs=last_round_idxs, meta=metas.append,
        client=FakeClient(response_text="x"), recent_files=[],
    )
    joined = "\n".join(metas)
    # Order matters — budget first, then snip, then micro.
    budget_pos = joined.find("persisted")
    snip_pos = joined.find("snipped")
    micro_pos = joined.find("replaced")
    assert 0 <= budget_pos < snip_pos < micro_pos


@pytest.mark.asyncio
async def test_run_proactive_triggers_llm_when_over_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    # context_window=10_000, max_output=4_096, headroom=1_000  →  threshold = 4_904
    # Need estimate > 4_904, i.e. > ~19_616 chars.
    hist = [Message(role="system", content="s"),
            Message(role="user", content="A" * 20_000)]
    metas = []
    await pipeline_mod.run_proactive(
        history=hist, profile=_profile(window=10_000, headroom=1_000),
        session_id="sid", last_round_tool_idxs=[], meta=metas.append,
        client=FakeClient(response_text="summary"), recent_files=[],
    )
    # llm_compact should have rewritten history.
    assert len(hist) == 2
    assert hist[1].role == "user"
    assert "Summary of prior conversation" in hist[1].content
    assert any("summarized history" in m for m in metas)


@pytest.mark.asyncio
async def test_run_proactive_force_summary_skips_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = [Message(role="system", content="s"),
            Message(role="user", content="tiny")]
    metas = []
    await pipeline_mod.run_proactive_force_summary(
        history=hist, profile=_profile(), session_id="sid",
        meta=metas.append, client=FakeClient(response_text="summary"),
        recent_files=[],
    )
    # Summary ran even though we're nowhere near the threshold.
    assert "Summary of prior conversation" in hist[1].content
    assert any("summarized history" in m for m in metas)


@pytest.mark.asyncio
async def test_child_thresholds_are_tighter():
    assert CHILD_THRESHOLDS.snip_threshold_messages < PARENT_THRESHOLDS.snip_threshold_messages
    assert CHILD_THRESHOLDS.snip_keep_head < PARENT_THRESHOLDS.snip_keep_head
    assert CHILD_THRESHOLDS.snip_keep_tail < PARENT_THRESHOLDS.snip_keep_tail


@pytest.mark.asyncio
async def test_run_reactive_delegates_to_reactive_run(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    hist = [Message(role="system", content="s")] + \
           [Message(role="user", content=f"u{i}") for i in range(20)]
    await pipeline_mod.run_reactive(
        history=hist, profile=_profile(), session_id="sid",
        meta=lambda _m: None,
        client=FakeClient(response_text="reactive summary"), recent_files=[],
    )
    # Layout: system + intro + tail
    assert hist[0].role == "system"
    assert hist[1].role == "user"
    assert "Reactive compact triggered" in hist[1].content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_pipeline.py -v`
Expected: ImportError on `codey.context.pipeline`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/context/pipeline.py`:

```python
"""Orchestrator: chains the 4 proactive steps + the reactive path.

`run_proactive` runs at the top of every model round inside Agent.run().
`run_proactive_force_summary` is what the /compact command and the
`compact` model tool call into. `run_reactive` runs from the except branch
when the provider returns PromptTooLong.

Thresholds are bundled into a Thresholds dataclass; parents use
PARENT_THRESHOLDS, children use CHILD_THRESHOLDS.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from openai import AsyncOpenAI

from ..config import Profile
from ..core.messages import Message
from . import budget as _budget
from . import llm as _llm
from . import micro as _micro
from . import reactive as _reactive
from . import snip as _snip
from . import tokens as _tokens

MetaSink = Callable[[str], None]


@dataclass(frozen=True)
class Thresholds:
    tool_result_budget_bytes: int
    snip_threshold_messages: int
    snip_keep_head: int
    snip_keep_tail: int
    micro_keep_recent_tool_results: int


PARENT_THRESHOLDS = Thresholds(
    tool_result_budget_bytes=_budget.TOOL_RESULT_BUDGET_BYTES,
    snip_threshold_messages=_snip.SNIP_THRESHOLD_MESSAGES,
    snip_keep_head=_snip.SNIP_KEEP_HEAD,
    snip_keep_tail=_snip.SNIP_KEEP_TAIL,
    micro_keep_recent_tool_results=_micro.MICRO_KEEP_RECENT_TOOL_RESULTS,
)

CHILD_THRESHOLDS = Thresholds(
    tool_result_budget_bytes=_budget.TOOL_RESULT_BUDGET_BYTES,
    snip_threshold_messages=30,
    snip_keep_head=3,
    snip_keep_tail=25,
    micro_keep_recent_tool_results=5,
)


def _should_llm_compact(history: list[Message], profile: Profile) -> bool:
    threshold = profile.context_window - profile.max_output_tokens - profile.compact_headroom
    return _tokens.estimate(history) > max(threshold, 0)


async def run_proactive(
    *,
    history: list[Message],
    profile: Profile,
    session_id: str,
    last_round_tool_idxs: list[int],
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
    thresholds: Thresholds = PARENT_THRESHOLDS,
) -> None:
    """Run the 4-step proactive pipeline. Mutates `history` in place."""
    # Step 1: budget (uses parent/child constant directly; threshold is bytes).
    _budget.run(
        history=history, last_round_tool_idxs=last_round_tool_idxs,
        session_id=session_id, meta=meta,
    )
    # Step 2: snip (note: snip uses its module constants; for child override
    # we monkey-patch with a wrapper — see _snip_with).
    _snip_with(history=history, meta=meta, thresholds=thresholds)
    # Step 3: micro.
    _micro_with(history=history, meta=meta, thresholds=thresholds)
    # Step 4: llm — only if still over threshold.
    if _should_llm_compact(history, profile):
        await _llm.run(
            history=history, profile=profile, session_id=session_id,
            meta=meta, client=client, recent_files=list(recent_files),
        )


def _snip_with(*, history, meta, thresholds: Thresholds) -> int:
    """Run snip with parameterized thresholds (the snip module's constants
    are the parent defaults; for child runs we adjust temporarily)."""
    if (thresholds.snip_threshold_messages == _snip.SNIP_THRESHOLD_MESSAGES
            and thresholds.snip_keep_head == _snip.SNIP_KEEP_HEAD
            and thresholds.snip_keep_tail == _snip.SNIP_KEEP_TAIL):
        return _snip.run(history=history, meta=meta)
    # Tighter child thresholds: do the snip with the alternate constants
    # by calling the underlying helpers directly.
    sys_count = sum(1 for m in history if m.role == "system")
    body = history[sys_count:]
    if len(body) <= thresholds.snip_threshold_messages:
        return 0
    prefix_end = _snip._expand_prefix_to_pair_boundary(body, thresholds.snip_keep_head)
    suffix_start = _snip._expand_suffix_to_pair_boundary(
        body, len(body) - thresholds.snip_keep_tail,
    )
    if suffix_start <= prefix_end:
        return 0
    dropped = suffix_start - prefix_end
    if dropped <= 0:
        return 0
    marker = Message(
        role="user",
        content=f"[... {dropped} earlier message"
                f"{'s' if dropped > 1 else ''} compacted by snip ...]",
    )
    history[sys_count:] = body[:prefix_end] + [marker] + body[suffix_start:]
    if meta:
        meta(f"[ctx: snipped {dropped} middle messages]")
    return dropped


def _micro_with(*, history, meta, thresholds: Thresholds) -> int:
    """Run micro with parameterized keep-count."""
    if thresholds.micro_keep_recent_tool_results == _micro.MICRO_KEEP_RECENT_TOOL_RESULTS:
        return _micro.run(history=history, meta=meta)
    keep = thresholds.micro_keep_recent_tool_results
    tool_idxs = [i for i, m in enumerate(history) if m.role == "tool"]
    if len(tool_idxs) <= keep:
        return 0
    cutoff = len(tool_idxs) - keep
    replaced = 0
    for i in tool_idxs[:cutoff]:
        if history[i].content != _micro.PLACEHOLDER:
            history[i].content = _micro.PLACEHOLDER
            replaced += 1
    if meta and replaced:
        meta(f"[ctx: replaced {replaced} old tool result"
             f"{'s' if replaced > 1 else ''} with placeholder]")
    return replaced


async def run_proactive_force_summary(
    *,
    history: list[Message],
    profile: Profile,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
    thresholds: Thresholds = PARENT_THRESHOLDS,
) -> None:
    """Run steps 1-3 then unconditionally run llm_compact. Used by the
    `compact` model tool and the /compact slash command."""
    _budget.run(history=history, last_round_tool_idxs=[],
                session_id=session_id, meta=meta)
    _snip_with(history=history, meta=meta, thresholds=thresholds)
    _micro_with(history=history, meta=meta, thresholds=thresholds)
    await _llm.run(
        history=history, profile=profile, session_id=session_id,
        meta=meta, client=client, recent_files=list(recent_files),
    )


async def run_reactive(
    *,
    history: list[Message],
    profile: Profile,
    session_id: str,
    meta: MetaSink | None,
    client: AsyncOpenAI,
    recent_files: Iterable[Path],
) -> None:
    """Wrapper around reactive.run so callers import from one place."""
    await _reactive.run(
        history=history, profile=profile, session_id=session_id,
        meta=meta, client=client, recent_files=list(recent_files),
    )
```

- [ ] **Step 4: Update `__init__.py` to re-export the public surface**

Replace `src/codey/context/__init__.py` with:

```python
"""Context management for the agent loop.

A 4-step proactive compaction pipeline plus a reactive retry path. The
pipeline runs at the top of every model round inside Agent.run() and is
designed so cheap steps are no-ops on small histories.

Steps (in order):
  1. tool_result_budget — persist >200kb tool results to disk
  2. snip_compact       — trim middle of conversation past 50 messages
  3. micro_compact      — placeholder old tool results, keep last 5 bodies
  4. llm_compact_history — single API call summary (only past headroom)

Failure path:
  reactive_compact      — runs on PromptTooLongError, ≤1 retry per turn

See docs/2026-06-07-context-management-design.md for the full spec.
"""
from __future__ import annotations

from .errors import PromptTooLongError, sniff
from .pipeline import (
    CHILD_THRESHOLDS,
    PARENT_THRESHOLDS,
    Thresholds,
    run_proactive,
    run_proactive_force_summary,
    run_reactive,
)

__all__ = [
    "PromptTooLongError",
    "sniff",
    "Thresholds",
    "PARENT_THRESHOLDS",
    "CHILD_THRESHOLDS",
    "run_proactive",
    "run_proactive_force_summary",
    "run_reactive",
]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_context_pipeline.py tests/test_context_*.py -v`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add src/codey/context/pipeline.py src/codey/context/__init__.py tests/test_context_pipeline.py
git commit -m "$(cat <<'EOF'
context: orchestrator + Thresholds + public re-exports

run_proactive chains the 4 steps in order. The llm step only fires past
the headroom-adjusted threshold (estimate(hist) > context_window -
max_output_tokens - compact_headroom). run_proactive_force_summary skips
the threshold for /compact + the compact tool. run_reactive proxies into
the reactive module.

Thresholds dataclass + PARENT/CHILD presets so sub-agent thresholds
can be passed in at orchestration time without monkey-patching module
constants permanently.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — Wire into Agent, streaming, Session, UI

This phase touches existing files. Each task ends with the full suite green so the system stays usable mid-phase.

### Task 12: recent_reads hook (`hooks/builtin/recent_reads.py`)

**Files:**
- Create: `src/codey/hooks/builtin/recent_reads.py`
- Test: `tests/test_recent_reads_hook.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_recent_reads_hook.py`:

```python
"""Tests for the recent_reads PostToolUse hook."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from codey.hooks.builtin.recent_reads import build_recent_reads_hook


@pytest.mark.asyncio
async def test_records_successful_read_file():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    await hook({
        "tool": "read_file",
        "arguments": {"path": "/tmp/a.txt"},
        "ok": True,
        "result": "...",
        "call_id": "c1",
    })
    assert list(dq) == [Path("/tmp/a.txt")]


@pytest.mark.asyncio
async def test_ignores_other_tools():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    await hook({"tool": "bash", "arguments": {"command": "ls"},
                "ok": True, "result": "...", "call_id": "c"})
    assert list(dq) == []


@pytest.mark.asyncio
async def test_ignores_failed_read_file():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    await hook({"tool": "read_file", "arguments": {"path": "/tmp/x"},
                "ok": False, "result": "error", "call_id": "c"})
    assert list(dq) == []


@pytest.mark.asyncio
async def test_dedupes_by_path():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    for p in ("a", "b", "a", "c"):
        await hook({"tool": "read_file", "arguments": {"path": p},
                    "ok": True, "result": "...", "call_id": "c"})
    # 'a' moved to the end on its second access.
    assert [str(x) for x in dq] == ["b", "a", "c"]


@pytest.mark.asyncio
async def test_respects_maxlen():
    dq: deque = deque(maxlen=3)
    hook = build_recent_reads_hook(dq)
    for p in ("a", "b", "c", "d", "e"):
        await hook({"tool": "read_file", "arguments": {"path": p},
                    "ok": True, "result": "...", "call_id": "c"})
    assert [str(x) for x in dq] == ["c", "d", "e"]


@pytest.mark.asyncio
async def test_skips_missing_path_arg():
    dq: deque = deque(maxlen=5)
    hook = build_recent_reads_hook(dq)
    await hook({"tool": "read_file", "arguments": {},
                "ok": True, "result": "...", "call_id": "c"})
    assert list(dq) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_recent_reads_hook.py -v`
Expected: ImportError on `codey.hooks.builtin.recent_reads`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/hooks/builtin/recent_reads.py`:

```python
"""recent_reads: track successful read_file paths on a deque.

Reads from PostToolUse payloads. The agent's deque (created in
Session.build) is the same one llm_compact_history re-reads files from
at compaction time.

Dedupe-by-path: re-reading a path moves it to the end of the deque, so
the "5 most recent" set is the 5 most recently-touched distinct files.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Callable

from ..registry import HookResult


def build_recent_reads_hook(dq: "deque[Path]") -> Callable[[dict[str, Any]], None]:
    def _hook(payload: dict[str, Any]):
        if payload.get("tool") != "read_file":
            return None
        if not payload.get("ok"):
            return None
        path_str = (payload.get("arguments") or {}).get("path")
        if not isinstance(path_str, str) or not path_str.strip():
            return None
        p = Path(path_str)
        try:
            dq.remove(p)
        except ValueError:
            pass
        dq.append(p)
        return None
    return _hook
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_recent_reads_hook.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/codey/hooks/builtin/recent_reads.py tests/test_recent_reads_hook.py
git commit -m "$(cat <<'EOF'
hooks: recent_reads PostToolUse hook

Tracks successful read_file paths on a caller-supplied deque (the
agent's per-session window). llm_compact_history pulls the latest N
entries to re-read at compaction time so the post-compact context
reflects the current file state.

Dedupe-by-path: re-reading moves the path to the end of the window.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Agent gains context fields + streaming wraps provider errors

**Files:**
- Modify: `src/codey/core/turn.py` (Agent dataclass fields only — wiring is Task 14)
- Modify: `src/codey/core/streaming.py` (wrap stream errors)
- Test: `tests/test_streaming_error_sniff.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_streaming_error_sniff.py`:

```python
"""Verify codey.core.streaming maps provider context-overflow errors to
PromptTooLongError."""
from __future__ import annotations

import pytest

from codey.config import Profile
from codey.context.errors import PromptTooLongError
from codey.core import streaming as streaming_mod


class FakeOpenAIError(Exception):
    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class FakeChatCompletions:
    def __init__(self, exc):
        self.exc = exc

    async def create(self, **kwargs):
        raise self.exc


class FakeClient:
    def __init__(self, exc):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeChatCompletions(exc)


def _profile():
    return Profile(name="p", api_key="k", base_url="x", model="m")


@pytest.mark.asyncio
async def test_stream_one_round_wraps_context_length_exceeded():
    client = FakeClient(FakeOpenAIError(
        "This model's maximum context length is X tokens",
        code="context_length_exceeded",
    ))
    with pytest.raises(PromptTooLongError):
        async for _ in streaming_mod.stream_one_round(client, _profile(), [], []):
            pass


@pytest.mark.asyncio
async def test_stream_one_round_wraps_http_413():
    client = FakeClient(FakeOpenAIError("too big", status_code=413))
    with pytest.raises(PromptTooLongError):
        async for _ in streaming_mod.stream_one_round(client, _profile(), [], []):
            pass


@pytest.mark.asyncio
async def test_stream_one_round_lets_unrelated_errors_through():
    class Boom(Exception):
        pass
    client = FakeClient(Boom("rate limit"))
    with pytest.raises(Boom):
        async for _ in streaming_mod.stream_one_round(client, _profile(), [], []):
            pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_streaming_error_sniff.py -v`
Expected: tests fail because streaming.py doesn't yet wrap errors.

- [ ] **Step 3: Patch `src/codey/core/streaming.py`**

At the top, add:

```python
from ..context.errors import PromptTooLongError, sniff as _sniff_provider_error
```

Wrap the `stream = await client.chat.completions.create(**kwargs)` line so the create call AND the async iteration are inside a try/except. Replace this block:

```python
    stream = await client.chat.completions.create(**kwargs)

    # tool_calls stream in fragments keyed by `index`; reassemble here.
    partial: dict[int, dict[str, Any]] = {}

    async for chunk in stream:
```

with:

```python
    try:
        stream = await client.chat.completions.create(**kwargs)
    except BaseException as exc:
        sniffed = _sniff_provider_error(exc)
        if sniffed is not None:
            raise sniffed from exc
        raise

    # tool_calls stream in fragments keyed by `index`; reassemble here.
    partial: dict[int, dict[str, Any]] = {}

    try:
        async for chunk in stream:
```

…and wrap the `async for chunk in stream:` body with a matching `except BaseException as exc: raise _sniff_provider_error(exc) or exc from exc`. Result (showing only the modified loop):

```python
    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                yield AssistantTextDelta(text=delta.content)

            for tc in delta.tool_calls or []:
                slot = partial.setdefault(
                    tc.index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["function"]["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["function"]["arguments"] += tc.function.arguments
    except PromptTooLongError:
        raise
    except BaseException as exc:
        sniffed = _sniff_provider_error(exc)
        if sniffed is not None:
            raise sniffed from exc
        raise
```

- [ ] **Step 4: Add Agent fields in `src/codey/core/turn.py`**

Add the import at the top:

```python
from collections import deque
from pathlib import Path
from typing import Callable
```

(Existing imports may already cover some of these — only add what's missing.)

Extend the Agent dataclass with three new fields (place them after `history`):

```python
@dataclass
class Agent:
    profile: Profile
    system_prompt: str = ""
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    hooks: HookRegistry = field(default_factory=HookRegistry)
    history: list[Message] = field(default_factory=list)
    session_id: str = ""
    _meta: Callable[[str], None] | None = None
    _recent_reads: "deque[Path]" = field(default_factory=lambda: deque(maxlen=5))
    _client: AsyncOpenAI = field(init=False)
```

- [ ] **Step 5: Run the suite**

Run: `uv run pytest tests/test_streaming_error_sniff.py tests/test_agent_recovery.py tests/test_concurrent_dispatch.py -v`
Expected: new tests pass; pre-existing agent/recovery tests still pass (Agent default construction still works because the new fields all have defaults).

- [ ] **Step 6: Full suite check**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/codey/core/streaming.py src/codey/core/turn.py tests/test_streaming_error_sniff.py
git commit -m "$(cat <<'EOF'
core: Agent context fields + streaming wraps provider errors

Agent dataclass gains session_id (for transcript paths), _meta (the
UI's meta_writer), and _recent_reads (deque used by llm_compact). All
default to safe empty values so existing tests construct Agents
unchanged.

streaming.stream_one_round now catches provider errors and re-raises
recognized context-overflow ones as PromptTooLongError so the reactive
path can act on a single exception type.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: Wire the pipeline into `Agent.run()`

**Files:**
- Modify: `src/codey/core/turn.py`
- Test: `tests/test_turn_context_integration.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_turn_context_integration.py`:

```python
"""End-to-end tests that Agent.run() invokes the context pipeline at
the top of each round and the reactive path on PromptTooLongError."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from codey.config import Profile
from codey.context.errors import PromptTooLongError
from codey.core.events import (
    AssistantMessageCompleted, AssistantTextDelta, TurnCompleted,
)
from codey.core.messages import Message
from codey.core.streaming import RoundDone
from codey.core.turn import Agent


def _profile():
    return Profile(name="p", api_key="k", base_url="https://x",
                   model="m", context_window=100_000,
                   max_output_tokens=4_096, compact_headroom=13_000)


@pytest.mark.asyncio
async def test_run_invokes_pipeline_at_top_of_round(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)
    calls = []

    async def fake_run_proactive(**kwargs):
        calls.append(("proactive", len(kwargs["history"])))

    monkeypatch.setattr("codey.context.run_proactive", fake_run_proactive)
    monkeypatch.setattr("codey.core.turn.context_pipeline.run_proactive", fake_run_proactive)

    agent = Agent(profile=_profile())

    async def fake_stream(self):
        yield AssistantTextDelta(text="hi")
        yield RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("hello"):
        events.append(ev)

    assert any(c[0] == "proactive" for c in calls)
    assert any(isinstance(e, TurnCompleted) and e.reason == "stop" for e in events)


@pytest.mark.asyncio
async def test_run_retries_once_on_prompt_too_long(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    reactive_called = []

    async def fake_reactive(**kwargs):
        reactive_called.append(True)

    monkeypatch.setattr("codey.core.turn.context_pipeline.run_reactive", fake_reactive)

    agent = Agent(profile=_profile())

    fail_count = {"n": 0}

    async def fake_stream(self):
        fail_count["n"] += 1
        if fail_count["n"] == 1:
            raise PromptTooLongError("too big")
        yield AssistantTextDelta(text="ok")
        yield RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("hello"):
        events.append(ev)

    assert reactive_called == [True]
    assert fail_count["n"] == 2
    assert any(isinstance(e, TurnCompleted) and e.reason == "stop" for e in events)


@pytest.mark.asyncio
async def test_run_surfaces_error_after_one_reactive_retry(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    async def fake_reactive(**kwargs):
        pass

    monkeypatch.setattr("codey.core.turn.context_pipeline.run_reactive", fake_reactive)

    agent = Agent(profile=_profile())

    async def fake_stream(self):
        raise PromptTooLongError("still too big")
        yield

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("hello"):
        events.append(ev)
    last = events[-1]
    assert isinstance(last, TurnCompleted)
    assert last.reason == "error"
    assert "PromptTooLong" in (last.error or "")


@pytest.mark.asyncio
async def test_run_breaks_round_loop_when_only_compact_called(monkeypatch, tmp_path):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    async def noop(**kwargs):
        pass

    monkeypatch.setattr("codey.core.turn.context_pipeline.run_proactive", noop)

    agent = Agent(profile=_profile())

    # Register a fake compact tool so dispatch finds it.
    class FakeCompact:
        name = "compact"
        description = ""
        parameters = {"type": "object", "properties": {}}
        async def run(self, args):
            return "[Compacted. History summarized.]"
    agent.tools.register(FakeCompact())

    round_count = {"n": 0}

    async def fake_stream(self):
        round_count["n"] += 1
        if round_count["n"] == 1:
            yield AssistantTextDelta(text="ok let me compact")
            yield RoundDone(tool_calls=[{
                "id": "c1", "type": "function",
                "function": {"name": "compact", "arguments": "{}"},
            }])
        else:
            yield AssistantTextDelta(text="more")
            yield RoundDone(tool_calls=[])

    monkeypatch.setattr(Agent, "_stream_one_round", fake_stream)

    events = []
    async for ev in agent.run("compact please"):
        events.append(ev)
    # Should have run exactly ONE round and then ended the turn.
    assert round_count["n"] == 1
    assert any(isinstance(e, TurnCompleted) and e.reason == "stop" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_turn_context_integration.py -v`
Expected: fails — pipeline not wired in yet.

- [ ] **Step 3: Patch `src/codey/core/turn.py` — Agent.run()**

Add import at the top:

```python
from .. import context as context_pipeline
from ..context.errors import PromptTooLongError
```

Inside `Agent.run()`, after the line `self.history.append(Message(role="user", content=user_input))` and before `yield TurnStarted()`, add:

```python
        reactive_retries = 0
        last_round_tool_idxs: list[int] = []
```

Inside the for-loop, at the top (BEFORE `yield RoundStarted(round=round_idx)`), add the proactive call:

```python
            try:
                await context_pipeline.run_proactive(
                    history=self.history,
                    profile=self.profile,
                    session_id=self.session_id,
                    last_round_tool_idxs=last_round_tool_idxs,
                    meta=self._meta,
                    client=self._client,
                    recent_files=list(self._recent_reads),
                )
            except Exception as e:  # noqa: BLE001
                # Pipeline errors are never fatal to the turn.
                if self._meta:
                    self._meta(f"[ctx: pipeline error: {type(e).__name__}: {e}]")
```

Wrap the existing streaming block in a try that catches `PromptTooLongError`. Replace this block:

```python
                text, tool_calls = "", []
                async for ev in self._stream_one_round():
                    if isinstance(ev, AssistantTextDelta):
                        text += ev.text
                        yield ev
                    elif isinstance(ev, streaming_mod.RoundDone):
                        tool_calls = ev.tool_calls
                        break
```

with:

```python
                text, tool_calls = "", []
                try:
                    async for ev in self._stream_one_round():
                        if isinstance(ev, AssistantTextDelta):
                            text += ev.text
                            yield ev
                        elif isinstance(ev, streaming_mod.RoundDone):
                            tool_calls = ev.tool_calls
                            break
                except PromptTooLongError as e:
                    if reactive_retries < 1:
                        try:
                            await context_pipeline.run_reactive(
                                history=self.history,
                                profile=self.profile,
                                session_id=self.session_id,
                                meta=self._meta,
                                client=self._client,
                                recent_files=list(self._recent_reads),
                            )
                        except Exception as inner:  # noqa: BLE001
                            if self._meta:
                                self._meta(f"[ctx: reactive failed: {type(inner).__name__}: {inner}]")
                            raise e
                        reactive_retries += 1
                        # Skip the rest of this round body; the outer for-loop
                        # will start a fresh round with the compacted history.
                        continue
                    raise
```

Right AFTER the existing `per_call_events = await asyncio.gather(...)` plus the `for events in per_call_events: for ev in events: yield ev` block — i.e. once all tool results have been yielded — record the indices of this round's tool messages and check for the lone-compact break:

```python
                # Record this round's tool-message indices for the next
                # round's tool_result_budget check.
                last_round_tool_idxs = [
                    i for i in range(len(self.history))
                    if self.history[i].role == "tool" and
                       self.history[i].tool_call_id in {c["id"] for c in tool_calls if c.get("id")}
                ]

                # If the model's only tool call was `compact`, end the turn so
                # the next user prompt sees the compacted context.
                if len(tool_calls) == 1 and tool_calls[0].get("function", {}).get("name") == "compact":
                    break
```

- [ ] **Step 4: Run integration tests**

Run: `uv run pytest tests/test_turn_context_integration.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the existing turn tests to ensure no regression**

Run: `uv run pytest tests/test_agent_recovery.py tests/test_concurrent_dispatch.py tests/test_hooks.py -v`
Expected: all pre-existing tests still green.

- [ ] **Step 6: Full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/codey/core/turn.py tests/test_turn_context_integration.py
git commit -m "$(cat <<'EOF'
core: wire context pipeline into Agent.run()

At the top of each model round call context_pipeline.run_proactive on
the current history. On PromptTooLongError from the streaming layer,
run context_pipeline.run_reactive and retry once; on a second failure
let the existing except handler surface the error.

After each tool-dispatch round, record the indices of this round's
tool-result messages so the next round's tool_result_budget knows
which messages to consider. If the round's only tool call was the
compact tool, break out of the round loop so the next user prompt
sees the compacted context.

Pipeline errors are swallowed with a meta line — context management
must never fail a turn that would otherwise have worked.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: CompactTool (`tools/compact.py`)

**Files:**
- Create: `src/codey/tools/compact.py`
- Modify: `src/codey/tools/__init__.py` (export only — registration in Session.build in Task 16)
- Test: `tests/test_compact_tool.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_compact_tool.py`:

```python
"""Tests for the model-callable `compact` tool."""
from __future__ import annotations

from collections import deque

import pytest

from codey.config import Profile
from codey.core.messages import Message
from codey.core.turn import Agent
from codey.tools.compact import CompactTool

from tests.test_context_llm import FakeClient


def _profile():
    return Profile(name="p", api_key="k", base_url="x", model="m",
                   context_window=100_000, max_output_tokens=4_096,
                   compact_headroom=13_000)


@pytest.mark.asyncio
async def test_compact_tool_returns_canonical_string(tmp_path, monkeypatch):
    monkeypatch.setattr("codey.context.transcripts._CACHE_ROOT", tmp_path)

    agent = Agent(profile=_profile(), session_id="sid")
    agent.history.extend([Message(role="user", content="hi")])
    # Force the client to be our fake so the summary call doesn't go out.
    agent._client = FakeClient(response_text="summary text")

    class FakeSession:
        def __init__(self, ag):
            self.agent = ag
            self.session_id = ag.session_id

    sess = FakeSession(agent)
    tool = CompactTool(session_provider=lambda: sess)
    out = await tool.run({})
    assert out == "[Compacted. History summarized.]"
    # And history was actually compacted.
    assert agent.history[-1].role == "user"
    assert "Summary of prior conversation" in agent.history[-1].content


@pytest.mark.asyncio
async def test_compact_tool_errors_when_unwired():
    tool = CompactTool(session_provider=None)
    out = await tool.run({})
    assert out.startswith("error:")


def test_compact_tool_schema_has_no_required_params():
    tool = CompactTool(session_provider=None)
    assert tool.name == "compact"
    assert tool.parameters["properties"] == {}
    assert "required" not in tool.parameters or not tool.parameters["required"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compact_tool.py -v`
Expected: ImportError on `codey.tools.compact`.

- [ ] **Step 3: Write the implementation**

Create `src/codey/tools/compact.py`:

```python
"""CompactTool: model-callable trigger for proactive context summary.

Like spawn_agent, this tool needs the live Session because it acts on
the agent's history + client + meta_writer + recent_reads. It's
registered from Session.build AFTER the Session exists.

Pure in the contractual sense: no permission logic, no UI rendering,
returns a string. The orchestrator (Agent.run) interprets a single
`compact` tool call as a turn-ending signal so the next user prompt
sees the compacted context.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .. import context as context_pipeline

if TYPE_CHECKING:
    from ..core.session import Session


@dataclass
class CompactTool:
    session_provider: Callable[[], "Session"] | None = None

    name: str = "compact"
    description: str = (
        "Force compact the conversation history NOW. Summarizes prior turns "
        "into a short message and re-injects the most recent files you read. "
        "After this returns, the current turn ends; you'll see the compacted "
        "context on the next user message. Call this when the conversation "
        "is long and you want a clean slate while preserving what you've "
        "learned. Call this tool ALONE in your turn — do not mix with other "
        "tool calls."
    )
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    })

    async def run(self, arguments: dict[str, Any]) -> str:
        sess = self.session_provider() if self.session_provider else None
        if sess is None:
            return "error: compact tool is not wired to a session"
        agent = sess.agent
        try:
            await context_pipeline.run_proactive_force_summary(
                history=agent.history,
                profile=agent.profile,
                session_id=agent.session_id,
                meta=agent._meta,
                client=agent._client,
                recent_files=list(agent._recent_reads),
            )
        except Exception as e:  # noqa: BLE001
            return f"error: compact failed: {type(e).__name__}: {e}"
        if agent._meta:
            agent._meta("[ctx: forced compaction by model]")
        return "[Compacted. History summarized.]"
```

- [ ] **Step 4: Export from `src/codey/tools/__init__.py`**

Add the import + export (do NOT register in `build_default_registry` — Session.build wires it):

```python
from .compact import CompactTool
```

And include `"CompactTool"` in the `__all__` list.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_compact_tool.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/codey/tools/compact.py src/codey/tools/__init__.py tests/test_compact_tool.py
git commit -m "$(cat <<'EOF'
tools: CompactTool — model-callable force-summary

Returns the canonical "[Compacted. History summarized.]" string after
calling context_pipeline.run_proactive_force_summary on the live
agent's history. Like spawn_agent, registered from Session.build with
a session_provider because it needs the live Session.

Agent.run already interprets a lone `compact` tool call as a turn-
ending signal (see turn.py break-on-compact).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Session.build wires session_id, meta, recent_reads, CompactTool, recent_reads hook

**Files:**
- Modify: `src/codey/core/session.py`
- Modify: `src/codey/hooks/builtin/__init__.py`
- Test: `tests/test_session_context_wiring.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_context_wiring.py`:

```python
"""Verify Session.build wires every context-related piece onto the Agent."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from codey.core.session import Session


class FakeSinks:
    def __init__(self):
        self.transcript_writer = None
        self.meta_lines = []
        self.meta_writer = self.meta_lines.append
        self.todo_writer = None

    async def approve(self, ctx):
        from codey.permissions import Verdict
        return Verdict.allow_once()


def test_agent_session_id_matches_session(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert sess.agent.session_id == sess.session_id
    assert len(sess.session_id) == 8


def test_agent_meta_is_wired(temp_config, tmp_path: Path):
    sinks = FakeSinks()
    sess = Session.build(profile_arg="alpha", ui_sinks=sinks, workspace=tmp_path)
    sess.agent._meta("[ctx: test]")
    assert "[ctx: test]" in sinks.meta_lines


def test_agent_recent_reads_is_deque(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert isinstance(sess.agent._recent_reads, deque)
    assert sess.agent._recent_reads.maxlen == 5


def test_compact_tool_registered(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert "compact" in sess.tools.tools


def test_child_does_not_get_compact_tool(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    child, _ = sess.build_child_agent(description="probe")
    assert "compact" not in child.tools.tools


def test_child_gets_own_recent_reads_deque(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    child, _ = sess.build_child_agent(description="probe")
    assert isinstance(child._recent_reads, deque)
    assert child._recent_reads is not sess.agent._recent_reads


def test_recent_reads_hook_registered_in_default_hooks(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    names = [h.name for h in sess.hooks.list()]
    assert "recent_reads" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_context_wiring.py -v`
Expected: most fail — Session doesn't wire any of this yet.

- [ ] **Step 3: Patch `src/codey/core/session.py`**

Add imports at the top:

```python
from collections import deque
from pathlib import Path  # if not already
```

In `Session.build`, change the `agent = Agent(...)` construction to set the new fields. Before `agent = Agent(...)`, leave hook construction as-is. Replace this block:

```python
        agent = Agent(
            profile=profile,
            system_prompt=build_system_prompt(skills=skills),
            tools=tools,
            hooks=hooks,
        )
        sess = cls(profile=profile, workspace=ws, cfg=cfg, agent=agent,
                   engine=engine, hooks=hooks, tools=tools,
                   session_id=session_id, skills=skills)
```

with:

```python
        agent = Agent(
            profile=profile,
            system_prompt=build_system_prompt(skills=skills),
            tools=tools,
            hooks=hooks,
            session_id=session_id,
            _meta=ui_sinks.meta_writer,
            _recent_reads=deque(maxlen=5),
        )
        sess = cls(profile=profile, workspace=ws, cfg=cfg, agent=agent,
                   engine=engine, hooks=hooks, tools=tools,
                   session_id=session_id, skills=skills)
```

After the existing `SpawnAgentTool` registration, add the CompactTool registration:

```python
        from ..tools.compact import CompactTool
        tools.register(CompactTool(session_provider=lambda: sess))
```

In `build_child_agent`, extend `EXCLUDED_FROM_CHILD` and add the recent_reads deque + child thresholds:

```python
        EXCLUDED_FROM_CHILD = {"spawn_agent", "todo_write", "compact"}
```

And construct the child Agent with the new fields:

```python
        child = Agent(
            profile=child_profile,
            system_prompt=child_system,
            tools=child_tools,
            hooks=child_hooks,
            session_id=child_id,
            _meta=self._meta_writer,
            _recent_reads=deque(maxlen=5),
        )
```

- [ ] **Step 4: Wire the recent_reads hook in `src/codey/hooks/builtin/__init__.py`**

Add import:

```python
from .recent_reads import build_recent_reads_hook
```

`build_default_hooks` needs the agent's deque. Easiest is to make the deque a required kwarg. Adjust the signature:

```python
def build_default_hooks(
    *,
    engine: PermissionEngine,
    approve: ApproveFn | None,
    transcript_writer: TranscriptWriter | None,
    meta_writer: MetaWriter,
    audit_log_path: Path | None = None,
    todo_tool: TodoWriteTool | None = None,
    todo_writer: TodoWriter | None = None,
    session_id: str | None = None,
    otel: dict | None = None,
    recent_reads_deque: Any | None = None,   # NEW
) -> HookRegistry:
```

And register the hook (after the other PostToolUse hooks) when the deque is provided:

```python
    if recent_reads_deque is not None:
        reg.register(HookEvent.POST_TOOL_USE,
                     build_recent_reads_hook(recent_reads_deque),
                     name="recent_reads")
```

Do the same for `build_child_hooks`.

Back in `core/session.py`, build the child's deque FIRST and pass it into the hook registry too. Refactor `Session.build` so the deque exists before `build_default_hooks` runs:

```python
        recent_reads = deque(maxlen=5)
        hooks = build_default_hooks(
            engine=engine,
            approve=ui_sinks.approve,
            transcript_writer=ui_sinks.transcript_writer,
            meta_writer=ui_sinks.meta_writer,
            todo_tool=tools.tools.get("todo_write"),
            todo_writer=ui_sinks.todo_writer,
            session_id=session_id,
            otel=otel_cfg,
            recent_reads_deque=recent_reads,
        )
        agent = Agent(
            profile=profile,
            system_prompt=build_system_prompt(skills=skills),
            tools=tools,
            hooks=hooks,
            session_id=session_id,
            _meta=ui_sinks.meta_writer,
            _recent_reads=recent_reads,
        )
```

And the same restructure inside `build_child_agent`:

```python
        child_recent_reads = deque(maxlen=5)
        child_hooks = build_child_hooks(
            engine=self.engine,
            approve=self._ui_approve,
            audit_log_path=self._audit_log_path,
            meta_writer=self._meta_writer,
            session_id=child_id,
            parent_session_id=self.session_id,
            description=description,
            recent_reads_deque=child_recent_reads,
        )
        ...
        child = Agent(
            profile=child_profile,
            system_prompt=child_system,
            tools=child_tools,
            hooks=child_hooks,
            session_id=child_id,
            _meta=self._meta_writer,
            _recent_reads=child_recent_reads,
        )
```

- [ ] **Step 5: Run the wiring tests**

Run: `uv run pytest tests/test_session_context_wiring.py -v`
Expected: 7 passed.

- [ ] **Step 6: Full suite**

Run: `uv run pytest`
Expected: all green. (Any pre-existing test that builds `build_default_hooks` directly without `recent_reads_deque` should still pass because the kwarg defaults to None.)

- [ ] **Step 7: Commit**

```bash
git add src/codey/core/session.py src/codey/hooks/builtin/__init__.py tests/test_session_context_wiring.py
git commit -m "$(cat <<'EOF'
session: wire context fields onto Agent + register CompactTool + recent_reads hook

Session.build:
  - creates the recent_reads deque,
  - passes it to build_default_hooks (so the recent_reads hook records
    every successful read_file path),
  - passes it + meta_writer + session_id onto the new Agent dataclass
    fields,
  - registers CompactTool(session_provider=lambda: sess).

build_child_agent does the same for the child, with its own deque, the
parent's meta_writer, and "compact" added to EXCLUDED_FROM_CHILD (sub-
agents inherit the auto-pipeline instead of the model-callable tool).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: `/compact` slash command

**Files:**
- Modify: `src/codey/ui/slash_commands.py`
- Modify: `src/codey/ui/app.py` (add `_cmd_compact`)
- Test: `tests/test_compact_slash.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_compact_slash.py`:

```python
"""Tests for the /compact slash command."""
from __future__ import annotations

from pathlib import Path

import pytest

from codey.ui.slash_commands import build_slash_commands


def test_compact_command_is_registered():
    cmds = build_slash_commands()
    assert "compact" in cmds
    assert "compact" in cmds["compact"].help.lower() or "summar" in cmds["compact"].help.lower()
```

Add a Textual-Pilot test extending the existing TUI test pattern (mirror `tests/test_tui.py` to keep the style consistent):

```python
import pytest

from codey.ui.app import CodeyApp


@pytest.mark.asyncio
async def test_compact_command_calls_compact_now(temp_config, tmp_path, monkeypatch):
    called = []

    async def fake_compact_now(self):
        called.append(True)

    from codey.core.turn import Agent
    monkeypatch.setattr(Agent, "compact_now", fake_compact_now)

    app = CodeyApp(profile_arg="alpha", workspace=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press(*list("/compact"))
        await pilot.press("enter")
        await pilot.pause()
    assert called == [True]
```

(If the existing TUI tests use a different invocation harness, mirror it. Check `tests/test_tui.py` first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_compact_slash.py -v`
Expected: the registration test fails (no /compact yet).

- [ ] **Step 3: Add `compact_now` to Agent in `src/codey/core/turn.py`**

Inside the Agent class, add:

```python
    async def compact_now(self) -> None:
        """Force-compact history right now. Used by the /compact slash command."""
        await context_pipeline.run_proactive_force_summary(
            history=self.history,
            profile=self.profile,
            session_id=self.session_id,
            meta=self._meta,
            client=self._client,
            recent_files=list(self._recent_reads),
        )
        if self._meta:
            self._meta("[ctx: forced compaction by /compact]")
```

- [ ] **Step 4: Add the slash command in `src/codey/ui/slash_commands.py`**

In `build_slash_commands()`, add (after the `skills` entry):

```python
        SlashCommand("compact",    "summarize history into a single message; preserves system prompt",
                     lambda app, _: app._cmd_compact()),
```

- [ ] **Step 5: Add `_cmd_compact` in `src/codey/ui/app.py`**

Add a method on `CodeyApp` (find a place near `_cmd_reset` or similar):

```python
    async def _cmd_compact(self) -> None:
        try:
            await self.session.agent.compact_now()
        except Exception as e:  # noqa: BLE001
            self._log_error(f"/compact failed: {type(e).__name__}: {e}")
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_compact_slash.py tests/test_tui.py -v`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/codey/ui/slash_commands.py src/codey/ui/app.py src/codey/core/turn.py tests/test_compact_slash.py
git commit -m "$(cat <<'EOF'
ui: /compact slash command + Agent.compact_now

Adds a user-facing trigger for proactive summary, parallel to the
model-callable `compact` tool. Useful when the user knows they're
about to ask something big and wants a clean slate while preserving
the conversation's accumulated context.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Sub-agents use CHILD_THRESHOLDS

**Files:**
- Modify: `src/codey/core/turn.py` — `Agent.run` accepts a `thresholds` parameter
- Modify: `src/codey/core/session.py` — child Agent passes child thresholds

**Note:** This is an additive optional parameter so existing tests don't change.

- [ ] **Step 1: Patch `Agent.run` in `src/codey/core/turn.py`**

Currently the pipeline call uses default `PARENT_THRESHOLDS`. Make this configurable on the Agent. Add a field to Agent:

```python
    context_thresholds: "context_pipeline.Thresholds" = field(
        default_factory=lambda: context_pipeline.PARENT_THRESHOLDS
    )
```

And pass it into the pipeline call:

```python
                await context_pipeline.run_proactive(
                    history=self.history,
                    profile=self.profile,
                    session_id=self.session_id,
                    last_round_tool_idxs=last_round_tool_idxs,
                    meta=self._meta,
                    client=self._client,
                    recent_files=list(self._recent_reads),
                    thresholds=self.context_thresholds,
                )
```

- [ ] **Step 2: Pass CHILD_THRESHOLDS in `Session.build_child_agent`**

In `src/codey/core/session.py`:

```python
        from .. import context as context_pipeline
        child = Agent(
            profile=child_profile,
            system_prompt=child_system,
            tools=child_tools,
            hooks=child_hooks,
            session_id=child_id,
            _meta=self._meta_writer,
            _recent_reads=child_recent_reads,
            context_thresholds=context_pipeline.CHILD_THRESHOLDS,
        )
```

- [ ] **Step 3: Add a test**

Add to `tests/test_session_context_wiring.py`:

```python
def test_child_uses_child_thresholds(temp_config, tmp_path: Path):
    from codey import context as context_pipeline
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    child, _ = sess.build_child_agent(description="probe")
    assert child.context_thresholds is context_pipeline.CHILD_THRESHOLDS
    assert sess.agent.context_thresholds is context_pipeline.PARENT_THRESHOLDS
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_session_context_wiring.py -v`
Expected: 8 passed (the 7 from Task 16 + the new one).

- [ ] **Step 5: Full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/codey/core/turn.py src/codey/core/session.py tests/test_session_context_wiring.py
git commit -m "$(cat <<'EOF'
core: per-Agent context thresholds + child uses CHILD_THRESHOLDS

Agent dataclass gains context_thresholds (default PARENT_THRESHOLDS).
Session.build_child_agent passes CHILD_THRESHOLDS (snip at 30 msgs,
keep head 3 / tail 25) so a focused sub-agent investigation hits the
trim path sooner than the long-running parent conversation.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 19: Documentation — CLAUDE.md "Where things go" table

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the context/ row to the table**

In `CLAUDE.md`, find the table that begins:

```
| If you're adding… | Put it in |
```

Add this row (after the "A new hook" row):

```
| A new context-pipeline step | `src/codey/context/<name>.py` + chain it in `pipeline.py:run_proactive` |
```

And add an entry to the "Architecture" tree listing under `src/codey/`:

```
  context/          # 4-step proactive compaction + reactive retry
    pipeline.py     #   orchestrator: run_proactive / run_proactive_force_summary / run_reactive
    budget.py snip.py micro.py llm.py reactive.py
    tokens.py transcripts.py errors.py
```

And add a "Context management" section in the same style as the existing "Sub-agents" / "Skills" sections, with a short paragraph plus key files list.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: CLAUDE.md — note the context-management package

Adds the context/ tree to the architecture listing, a short "Context
management" subsection mirroring the Sub-agents and Skills sections,
and a "Where things go" entry for new pipeline steps.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — Final verification

### Task 20: Smoke test the full pipeline manually

- [ ] **Step 1: Full suite + count check**

Run: `uv run pytest`
Expected: ~225 tests pass (196 existing + ~29 new). If the count is off by a couple either way, check for skipped/parametrized tests but accept ±5.

- [ ] **Step 2: TUI smoke test — long conversation hits snip**

Run `uv run codey -p <some-profile>`. Engage in a long back-and-forth (or send 60+ short messages). Expected meta line in the transcript:

```
[ctx: snipped N middle messages]
```

- [ ] **Step 3: TUI smoke test — big tool output hits budget**

Run a `bash` command that produces a lot of output, e.g.:

```
> bash -c 'for i in $(seq 1 200000); do echo "line $i"; done'
```

Expected meta line:

```
[ctx: persisted 1 tool result (Nkb) to disk]
```

Then check the file appeared:

```
ls ~/.cache/codey/transcripts/<session_id>/tool_results/
```

- [ ] **Step 4: TUI smoke test — `/compact`**

After several turns, type `/compact` in the input. Expected meta line(s):

```
[ctx: summarized history → 1 message (snapshot: ...)]
[ctx: forced compaction by /compact]
```

Verify the snapshot file exists:

```
ls ~/.cache/codey/transcripts/<session_id>/snapshots/
```

- [ ] **Step 5: TUI smoke test — model-callable `compact`**

Ask the model: "Please call the `compact` tool to clean up our history." Expected: turn ends, next user prompt sees a fresh-looking transcript with summarized context.

- [ ] **Step 6: TUI smoke test — sub-agent threshold**

In a session, ask the model to spawn a sub-agent that does a long-running investigation (e.g. "spawn a sub-agent to investigate the entire codebase and return a 5-paragraph summary"). The child's snip threshold (30) should kick in noticeably sooner than the parent's (50).

- [ ] **Step 7: Reactive path smoke test (optional, depends on provider)**

Set a very small `context_window` (e.g. 4000) on a profile, then push history past that. The provider should 413; meta line should appear:

```
[ctx: reactive compact triggered (kept last N msgs)]
```

If the reactive path fails to recover, you'll see:

```
[turn finished: error]
```

- [ ] **Step 8: Sanity-check the audit log**

Confirm the audit log still records everything:

```
tail -5 ~/.cache/codey/calls.jsonl | jq .
```

Should show normal tool-call lines unchanged by the context pipeline.

---

## Self-review

I cross-checked the plan against the spec section-by-section:

- ✅ tool_result_budget — Task 5 (with 200kb threshold + persisted-stub idempotence + disk-failure path)
- ✅ snip_compact — Task 6 (with both pair-boundary expanders + gap marker)
- ✅ micro_compact — Task 7 (placeholder, last 5 kept, idempotent)
- ✅ llm_compact_history — Task 9 (system + summary user + recent-file re-read + snapshot)
- ✅ reactive_compact — Task 10 (system + summary + last 5 expanded for pairs, snapshot)
- ✅ Profile fields — Task 8 (defaults 1M / 4096 / 13000)
- ✅ Orchestrator + Thresholds + child preset — Task 11
- ✅ run_proactive at top of each round + ≤1 reactive retry + break-on-compact — Task 14
- ✅ CompactTool — Task 15; /compact + compact_now — Task 17
- ✅ Recent_reads hook — Task 12
- ✅ Session wiring + child excluded from compact tool + child gets own deque — Task 16, Task 18
- ✅ Streaming sniffer for PromptTooLongError — Task 13
- ✅ Meta lines on every non-trivial step — covered inside each module
- ✅ Disk layout under ~/.cache/codey/transcripts/<sid>/ — Task 4
- ✅ Documentation — Task 19
- ✅ Smoke tests — Task 20

No `TBD`/`TODO`/`fill in details` markers. Test code is concrete; commit messages are concrete; expected counts are concrete. Type names used in later tasks (`Thresholds`, `MetaSink`, `PromptTooLongError`, `CompactTool`, `Profile.context_window`) all line up with where they were introduced.

