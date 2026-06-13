# Memory for codey — design

Adds two persistence layers to codey:

1. **Session memory** — every chat is a session with a stable `session_id` and
   workspace. The full message history is appended to disk live, so the user
   can `/resume` a past conversation. Builds directly on the per-session
   transcript directory the context-management package already maintains.
2. **Long-term memory** — small, curated facts and preferences the model
   carries across sessions. Stored as one markdown file per entry with YAML
   frontmatter; an auto-rebuilt `MEMORY.md` index is injected into the system
   prompt; bodies are loaded on demand. Two tiers (global + project) with the
   same override semantics as skills and `codey.md`. New memories are
   extracted from the conversation by an LLM call fired on the Stop hook;
   each extraction is followed by an inline targeted-consolidate that
   deduplicates against existing entries before writing. A turn-start
   side-query selects up to 5 relevant entries to pre-load into the prompt.

References:
- Claude Code's `MEMORY.md` + per-entry `*.md` index pattern (the file the
  parent of this design lives next to).
- codex's session resumption flag.
- codey's own skills system (`src/codey/skills/`) — memory mirrors it almost
  beat-for-beat.

## Goals & non-goals

**Goals**
- Persist every session's full message history live so any session can be
  resumed, scoped to its original workspace + profile.
- Offer a cwd-scoped modal picker (`/resume`) plus a `codey --resume [sid]`
  CLI flag.
- Persist long-term memory as one file per entry across two tiers
  (`~/.config/codey/memory/`, `<workspace>/.codey/memory/`), with a
  derived-on-write `MEMORY.md` index.
- Inject the merged memory index into the system prompt at all times.
- At turn start, run a cheap LLM side-query to pick up to 5 relevant
  memories and inject their bodies into the prompt; keyword-match fallback
  on side-query failure; inject nothing when no entry is confidently
  relevant.
- Extract candidate memories from the just-finished turn on the Stop hook,
  in a fire-and-forget background task; run a targeted consolidate
  (read existing entries of the same `type`) before writing; deduplicate
  identical entries; supersede contradicting older entries.
- Always rebuild the tier's `MEMORY.md` after any write/delete/update.
- Offer explicit save paths: a `/remember <text>` slash command and a
  `remember_this` model tool, both alongside the auto-extractor.
- Tolerate crashes: if a Stop-hook extraction is interrupted, a small JSONL
  queue file lets the next session finish it.
- All flows fail soft. Errors emit a meta line; the conversation never breaks
  because of memory.

**Non-goals (v1)**
- No filesystem watcher / live reload — registry scans once at session start.
- No memory editor UI; entries are plain markdown on disk.
- No full-sweep periodic consolidate. Inline targeted consolidate per write
  is the only dedupe path in v1. A `/memory compact` full sweep is a v2
  follow-up.
- No `last_used_at` tracking or stale-entry expiry in v1.
- No vector embeddings or semantic retrieval. Side-query is one LLM call
  over the index; the registry holds nothing beyond the parsed frontmatter
  + body.
- No auto-resume of the most recent session on bare `codey`. Resume is
  always explicit (`--resume` or `/resume`).
- No memory in sub-agents writing to memory. Children can read (load_memory,
  see the index) but the extractor and `remember_this` tool are
  parent-only — the parent owns the long-term store, same way the parent
  owns the plan (`todo_write`).

---

## Part 1 — Session memory

### Behavior

- Every session has a `session_id` (8-hex, already exists). The transcript
  directory `~/.cache/codey/transcripts/<sid>/` (already exists for context
  spills and snapshots) gains two new files:
  - `meta.json` — `{session_id, workspace, profile, started_at, last_at,
    title, message_count}`. Rewritten on session start and after each
    `TurnCompleted`. `title` is the first user message, truncated to 60
    chars (LLM-generated titles deferred to v2).
  - `messages.jsonl` — one `Message.to_wire()` dict per line, appended live
    every time a message is added to `Agent.history`.
- `messages.jsonl` is append-only. The repair step that drops orphan
  `tool_calls` (`history._repair_history`) runs *in memory* at the top of
  `Agent.run()`; the on-disk JSONL still contains every line that was ever
  written. Resume replays the file and re-runs the repair, so a partial
  turn (crash mid-round) is healed automatically.
- `/resume` opens a modal picker listing sessions for the current
  workspace, most-recent first. The list is built by scanning
  `~/.cache/codey/transcripts/*/meta.json` and filtering to
  `meta.workspace == cwd`. Up to 20 entries shown; older paged.
- `codey --resume` lists the same picker on the terminal before the TUI
  starts; `codey --resume <sid>` skips the picker and loads directly.
- Resume restores: `history`, `cwd` (must still exist; clear error if not),
  `profile` (must still resolve; clear error if not). The context pipeline
  runs on the first round as usual — a resumed long session will compact on
  its first turn just like a fresh one would once it grew.
- The session file format is forward-compatible: unknown keys in `meta.json`
  are ignored on load (round-tripped through to writes).

### Layout

```
~/.cache/codey/transcripts/<session_id>/
  meta.json          # new: cwd, profile, started_at, last_at, title, message_count
  messages.jsonl     # new: one Message.to_wire() per line, appended live
  tool_results/      # existing — persisted oversized tool result bodies
  snapshots/         # existing — pre-compact history snapshots
```

The directory is created on first write (already true for tool_results /
snapshots). Permissions: 0o700 on the session directory, 0o600 on files,
best-effort same as `context/transcripts.py`.

### Module layout

```
src/codey/session_store/
  __init__.py         # re-export SessionStore + SessionMeta
  meta.py             # SessionMeta dataclass + read/write
  store.py            # SessionStore: list_for_workspace, load, save_meta,
                      #               append_message
  errors.py           # SessionResumeError (workspace gone, profile gone,
                      #                      corrupted jsonl)
```

### Integration

- `core/turn.py:Agent.run()` adds one call after every append to
  `self.history`: `self._session_store.append_message(msg)` if a store is
  attached. The hot loop is in `_stream_one_round` after the assistant
  message is finalized and after each tool result is appended; one helper
  on `Agent` (`_append_history`) centralizes both call sites so the disk
  write happens in exactly one place.
- `core/session.py:Session.build()` constructs a `SessionStore`, writes the
  initial `meta.json`, attaches the store to the `Agent` via a new field.
- `core/session.py:Session.build_resumed(sid)` — new classmethod that loads
  meta + jsonl, validates the workspace, swaps the profile, then runs the
  same wiring `build()` does. Resume reuses the same `session_id` so all
  subsequent writes append to the original directory (the resumed session
  is a continuation, not a fork).
- `ui/modals/resume_picker.py` — new modal mirroring `profile_picker.py`,
  populated by `SessionStore.list_for_workspace()`.
- `ui/slash_commands.py` — add `/resume`.
- `app.py:main()` — add `--resume [SID]` argparse flag; thread through to
  `CodeyApp.run()` which picks `Session.build_resumed` over `Session.build`.

### Sub-agents

Children do not write to their own session store; the parent's
`messages.jsonl` records the parent turn only. Child events still flow to
the `SubAgentRecorder` and to the audit log (per existing design). Resume
restores the parent history; sub-agent state was always ephemeral and
remains so.

---

## Part 2 — Long-term memory

### Behavior

- Memory is a key-value store: each entry is a markdown file. The
  **filename** is the entry name (`<name>.md`); the YAML frontmatter
  carries `description` (shown in the index), `type` (rough category,
  enum-ish but free string), `created_at`, `updated_at`, `source_session`.
  The body is the rule itself plus any "why" / "how to apply" the
  extractor recorded.
- A `MEMORY.md` per tier is the **index** — `(name, description)` bullets
  only. It is **always derived from the entry files** and rebuilt on any
  write/delete/update. Users may read it but should not author it.
- Tiers, scanned at session start (and after every memory mutation in this
  process):
  - `~/.config/codey/memory/` (global)
  - `<workspace>/.codey/memory/` (project)
- Override on name collision: project wins, global loser written to the
  audit log as `memory_override` (same JSONL the audit_log hook + skill
  override use).
- The merged index is appended to the system prompt as a fifth layer
  (default → user → project → skills → memory_index). When the registry
  is empty the layer is skipped.
- At the top of `Agent.run()`, before the first round's LLM call, run the
  **side-query** to select up to 5 relevant memories. Inject their bodies
  as a sixth layer (`_loaded_memories_layer`). When the side-query returns
  zero entries, no layer is injected.
- The model can also call `load_memory(name=...)` at any time to fetch a
  body it sees in the index. Mirrors `load_skill` exactly.
- The model can call `remember_this(name=..., description=..., body=...,
  type=?, scope="global"|"project")` to write a memory inline mid-turn.
  Default scope is `"project"`. Triggers the same write → consolidate →
  index-rebuild chain the extractor uses.
- The user can type `/remember <text>` to save freeform text. The slash
  handler runs the same pipeline (one LLM call to turn the text into a
  candidate entry, then consolidate, write, rebuild).
- On the Stop hook, a fire-and-forget background task runs the
  **extractor**: reads the just-finished turn's messages, asks the model
  for ≤N candidate entries, runs targeted consolidate per candidate,
  writes accepted ones, rebuilds the affected tier's index. The default
  scope for auto-extracted entries is decided by the extractor prompt
  (preference / style → global, project-specific facts → project).
- Crash safety: each Stop-hook extraction first appends one line to
  `~/.cache/codey/memory_queue.jsonl` describing what it's about to do
  (a snapshot of the turn's message ids). On successful completion the
  line is removed. On `Session.build()`, leftover queue entries are
  replayed in the background.
- All write paths route through `MemoryStore`. There is exactly one place
  that mutates `~/.config/codey/memory/` or `<ws>/.codey/memory/`.

### File shape

```markdown
---
name: user_prefers_tabs
description: indent with tabs, never spaces
type: preference
created_at: 2026-06-13T14:02
updated_at: 2026-06-13T14:02
source_session: a1b2c3d4
---

Always use tab characters for indentation — never spaces.
Confirmed by the user on 2026-06-13 after a 4-space commit was rejected.
```

Validation, mirroring `skills/io.py`:
- Filename must equal `name:` frontmatter (consistency check).
- `description:` required, non-empty.
- `type:` required, non-empty (free string; common values: `preference`,
  `project`, `fact`, `style`, `other`).
- `created_at:` and `updated_at:` required, ISO-8601.
- Body required, non-empty after strip.
- Bad files are skipped + logged `memory_invalid` to the audit log.

### Layout

```
src/codey/memory/
  __init__.py
  models.py           # Memory dataclass + MemoryType literal
  io.py               # parse_memory_md (reuse skills/io.py parser),
                      # write_memory, delete_memory, rebuild_index
  registry.py         # MemoryRegistry: scan two tiers, merge with project>global,
                      #                 list_meta -> "## Memory" index block
  store.py            # MemoryStore: the only writer; rebuilds index on every
                      #              mutation; logs audit lines
  select.py           # side-query: pick <=5 names from index given recent msgs;
                      #             keyword-match fallback
  extract.py          # Stop-hook extractor: LLM call -> candidate entries
  consolidate.py      # targeted consolidate: dedupe vs existing entries of same type
  queue.py            # ~/.cache/codey/memory_queue.jsonl: enqueue/replay
  errors.py
```

Three new tools (each in `src/codey/tools/`):
- `tools/load_memory.py` — `load_memory(name)` returns the body.
- `tools/remember_this.py` — `remember_this(name, description, body, type?,
  scope?)`. Parent-only (excluded from child tool registry, same way
  `todo_write` / `compact` are).
- (No `forget` tool in v1. Users edit/remove files manually; the registry
  re-scans next session.)

One new hook (`src/codey/hooks/builtin/`):
- `memory_extract.py` — STOP. Schedules
  `asyncio.create_task(_extract_and_write(turn_messages))` and returns.
  Fire-and-forget; the task uses the same OpenAI client as the agent but
  with `tool_choice="none"` and a small `max_output_tokens`.

One new slash command:
- `/remember <text>` — runs the same single-candidate path
  `remember_this` does; opens a confirm modal showing the parsed
  `(name, description, body, type, scope)` for user approval before
  writing.

### System prompt — new layers

Two layers append to the existing four (`prompt.py`):

5. **Memory index** (`_memory_index_layer`) — emitted whenever the merged
   `MemoryRegistry` is non-empty. Format mirrors the skills layer:

   ```
   ## Memory
   You have a long-term memory of facts and preferences learned across
   sessions. The names + descriptions below are always available; load
   a full entry with `load_memory(name=...)`. Use these to align with
   the user's preferences and project conventions.

   - **user_prefers_tabs** — indent with tabs, never spaces
   - **codey_uses_uv** — manage deps with uv, not pip
   - …
   ```

6. **Loaded memories** (`_loaded_memories_layer`) — emitted only when the
   side-query selected ≥1 entry for the current turn. Built per-turn,
   passed to `_stream_one_round` as an **extra leading system message**
   (not merged into the persistent `Agent.history[0]`). It exists only in
   the wire request for this turn; the next turn reruns the side-query
   and rebuilds the layer from scratch. Keeping it out of `history` avoids
   bloating the persisted `messages.jsonl` with per-turn-ephemeral content
   and keeps the layer free to disappear when irrelevant.

   ```
   ## Loaded memories (this turn)
   ### user_prefers_tabs
   Always use tab characters…
   ```

   Skipped entirely if the side-query returns 0 names.

### Side-query — turn-start selection

`select.py:pick_relevant(...)` runs once per turn before the first round:

1. Build a small prompt: `(merged index, last user message, optional last
   assistant message)`. Ask the LLM to return a JSON array of ≤5 names
   from the index, only including names where it is **confident** the
   entry is relevant. Empty array on uncertainty.
2. Strict JSON parse. On parse failure or any LLM error: fall back to
   keyword matching — tokenize the user message, score each index entry
   by overlap with `name` + `description`, take the top ≤5 entries with
   score ≥ a small threshold; return `[]` if nothing scores.
3. Return the chosen names (the layer-builder maps to bodies via the
   registry).

The side-query call uses the same model as the active profile. Cost is
small (index is tiny, output is a JSON list); no caching needed in v1.

### Extractor — Stop-hook chain

`memory_extract` hook callback:

1. Enqueue a queue line: `{turn_id, message_ids, started_at}`.
2. `asyncio.create_task(_extract_and_write(...))`. Return immediately so
   the turn's Stop processing completes.
3. Inside the task:
   1. `extract.py:propose_candidates(messages, existing_index) -> list`
      — one LLM call. Returns up to 3 candidates per turn; each is
      `{name, description, body, type, scope}`. Bias toward zero
      candidates when nothing is clearly worth saving (prompt says so
      explicitly).
   2. For each candidate, `consolidate.py:targeted_consolidate(...)`:
      - Load all existing entries of the same `type` from both tiers.
      - One LLM call: "given this new candidate and these existing
        entries, decide DUPLICATE (skip), UPDATE (rewrite which entry's
        body), SUPERSEDE (delete which old entries and write new), or
        NOVEL (write as-is)."
      - Apply the decision via `MemoryStore`.
   3. `MemoryStore.rebuild_index(tier)` after each mutation.
   4. Remove the queue line.
4. On any exception in the task: meta-line `[memory: extract failed: ...]`,
   queue line stays for next-session replay.

### MemoryStore — the only writer

Single chokepoint for all mutations. Public methods:

- `write(memory: Memory) -> Path`
- `update(name, scope, *, description?, body?, type?) -> Path`
- `delete(name, scope) -> bool`
- `rebuild_index(scope) -> Path`

Every method:
1. Writes the file (or removes it).
2. Updates `updated_at`.
3. Rebuilds the affected tier's `MEMORY.md` from a fresh disk scan.
4. Writes one audit line: `{event: "memory_write" | "memory_delete" |
   "memory_supersede", name, scope, source: "extract" | "tool" |
   "slash"}`.

The audit log path is the same `~/.cache/codey/calls.jsonl` everything
else uses, so `jq` can pull the full memory history of a session next to
its tool calls.

### Integration

- `core/session.py:Session.build()` — construct `MemoryRegistry.scan(...)`
  (mirrors `SkillRegistry.scan`), pass to:
  - `build_system_prompt(memory=registry, ...)` (new keyword)
  - the new tools' constructors
  - the `memory_extract` hook
  - `MemoryStore` (which shares the registry for in-process consistency)
- `prompt.py` — add `_memory_index_layer(memory)`; threaded through
  `build_system_prompt` and `build_subagent_system_prompt`. Sub-agents
  see the index (so they can read), but do not get `remember_this` and do
  not run the Stop-hook extractor.
- `tools/__init__.py:build_default_registry` — register `load_memory`.
  `Session.build` registers `remember_this` (parent-only) the same way it
  registers `compact` (parent-only).
- `hooks/builtin/__init__.py:build_default_hooks` — register
  `memory_extract` on STOP. `build_child_hooks` does not.
- `ui/slash_commands.py` — add `/remember`.
- `core/turn.py:Agent.run` — at the top of the first round, after context
  pipeline runs, call `select.pick_relevant(...)` and compose the
  loaded-memories layer; pass into `_stream_one_round` as an additional
  system message for this round only.
- `core/session.py:build_child_agent` — `EXCLUDED_FROM_CHILD` adds
  `remember_this`. Children inherit `load_memory`. The child system prompt
  includes the same memory index.

---

## Cross-cutting concerns

### Failure modes — never block the turn

Every disk write, LLM side-call, and extractor task is wrapped so that
failure emits one `[memory: ...]` meta line and otherwise leaves the
conversation untouched. Same posture as the context pipeline
(`[ctx: pipeline error: ...]`).

### Privacy

Memory files contain whatever the extractor decides to save from
conversations. The extractor prompt explicitly forbids saving secrets,
API keys, passwords, tokens, paths under `/secrets`, etc. On detection
of such content in a candidate, the candidate is dropped + a meta line
emitted.

### Concurrency

- `messages.jsonl` is append-only and only the agent writes it; no lock
  needed.
- `MemoryStore` writes are serialized through an `asyncio.Lock` per
  process. Cross-process: two `codey` instances writing to the same tier
  concurrently is rare; we accept last-write-wins and log a debug note if
  the rebuild detects a file appeared/disappeared between scan and write.
- The Stop-hook extractor task and the next turn's side-query can race
  if the user immediately types again. The side-query reads whatever
  `MemoryRegistry` looks like at that instant; new memories from the
  in-flight extract show up in the *following* turn. Acceptable.

### Config

Two new config keys under `[memory]` in `~/.config/codey/config.toml`,
all optional:
- `auto_extract = true`   — default; set false to disable Stop-hook
  extraction entirely.
- `side_query = true`     — default; set false to skip the turn-start
  side-query (index still in prompt).
- `max_loaded = 5`        — cap for the side-query.

No per-profile override in v1; one setting per user.

---

## Verification

End-to-end manual:

```bash
# Sessions
uv run codey                                  # start fresh
> hello
> /exit
uv run codey --resume                          # picker shows the session
uv run codey --resume <sid>                    # direct resume

# Long-term memory — explicit
uv run codey
> /remember always run `uv run pytest` after edits
# expect: meta line "↳ memory saved: always_run_pytest"
> /exit
ls ~/.config/codey/memory/                     # MEMORY.md + the entry
cat ~/.config/codey/memory/MEMORY.md           # index has the bullet

# Long-term memory — auto-extract
uv run codey
> never use console.log in this repo, prefer the logger module
# (do some work; let the turn end)
# expect: meta line about memory extraction
ls .codey/memory/                              # project-scoped entry written

# Side-query
uv run codey
> add a new dependency
# expect: the model brings up uv (because the codey_uses_uv entry pre-loaded)

# Crash safety
# kill codey mid-Stop-hook (Ctrl-C twice) and inspect:
ls ~/.cache/codey/memory_queue.jsonl
uv run codey                                   # queue replayed in background
```

Automated:

- `tests/test_session_store.py` — round-trip a small history through
  `messages.jsonl`, verify `_repair_history` heals a truncated final line,
  verify `list_for_workspace` filters by cwd, verify meta load/save.
- `tests/test_memory_io.py` — parse known-good files; reject malformed
  ones with the same shape `skills/io.py` does; consistency check on
  filename↔`name`.
- `tests/test_memory_registry.py` — two-tier scan, project shadows
  global, audit line written on override, malformed entries skipped.
- `tests/test_memory_store.py` — write/update/delete each rebuilds
  `MEMORY.md`; audit line per mutation; concurrent writes are serialized
  by the lock.
- `tests/test_memory_select.py` — happy path (LLM returns names),
  malformed-JSON fallback to keyword matching, empty fallback path,
  ≤5 cap respected.
- `tests/test_memory_extract.py` — extractor returns 0 / 1 / N
  candidates; targeted consolidate produces DUPLICATE / UPDATE /
  SUPERSEDE / NOVEL given each fixture; queue line removed on success,
  retained on failure.
- `tests/test_memory_prompt_layer.py` — index layer present iff registry
  non-empty; loaded-memories layer omitted when side-query returns 0;
  layer ordering preserved across all 6 layers.
- `tests/test_session_resume.py` — `Session.build_resumed` restores
  history + cwd + profile; raises `SessionResumeError` with a clear
  message when workspace is missing.

All new tests use the async fixtures already in `tests/conftest.py`; LLM
calls are stubbed via the same fake-client pattern existing context-pipeline
tests use.
