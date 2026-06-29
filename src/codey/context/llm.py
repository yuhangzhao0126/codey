"""Step 4: llm_compact_history — single API call summary.

Replaces the conversation body with a synthetic user message containing
the model's own summary of the prior history plus a re-read of the most
recent 5 files the agent looked at. System messages survive untouched.

Triggered by the orchestrator when estimate(history) >
provider.context_window - provider.max_output_tokens - provider.compact_headroom.

Same client + provider as the active agent. The summary call is non-
streaming, tool-less, low-temperature.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from openai import AsyncOpenAI

from ..config import Provider
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


async def _summarize(client: AsyncOpenAI, provider: Provider, history: list[Message]) -> str:
    transcript = _render_transcript(history)
    resp = await client.chat.completions.create(
        model=provider.model,
        stream=False,
        temperature=0.2,
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
    )
    return resp.choices[0].message.content or ""


def _read_recent_files(recent_files: Iterable[Path], max_files: int) -> list[tuple[Path, str]]:
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
    provider: Provider,
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

    summary = await _summarize(client, provider, history)
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
