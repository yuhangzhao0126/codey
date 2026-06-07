# CLAUDE.md

Operating notes for an AI coding agent (Claude / codey / etc.) working on
**codey itself**. Codey is the project in this repo: a terminal-based coding
agent written in Python. This file is the project-level system prompt layer
loaded automatically by codey (see `src/codey/prompt.py`) — but it's also
written so any agent doing development on this repo (Claude Code, codex,
cursor) gets the same picture.

If anything here contradicts what you find in the actual code, **the code
wins** — update this file in the same commit.

---

## What this project is

A terminal coding agent. Talks to any OpenAI-compatible API. Runs local tools
(bash, file read/write/grep, in-place edits) with a permission system. Single
front-end: a Textual full-screen TUI (`codey`). Built as a learning project —
small enough to read end-to-end, big enough to be useful for day-to-day
coding tasks.

Public surface for users is documented in `README.md`.

---

## Setup & common commands

```bash
uv sync                    # install deps into .venv
uv run pytest              # run the test suite (must stay green)
uv run codey               # Textual TUI
uv run codey -p deepseek   # pick a profile from ~/.config/codey/config.toml
```

Python **≥ 3.11** (uses `tomllib`). Use `uv add` / `uv remove` for deps;
`uv.lock` is committed.

---

## Architecture

The core data flow is one round of `Agent.run()`. Read `src/codey/core/turn.py`
top-to-bottom before changing any of it — the comments there are the source
of truth.

```
src/codey/
  app.py            # Entry point: argparse + CodeyApp.run()
  __main__.py       # `python -m codey` shim

  core/             # Agent loop + message/event types + Session bundle
    turn.py         #   Agent.run() = the turn loop. Multi-round dispatch,
                    #   history repair, error rollback, cancellation.
    streaming.py    #   _stream_one_round + tool_call fragment reassembly
    history.py      #   _repair_history (free function, independently tested)
    messages.py     #   Role, Message, Message.to_wire()
    events.py       #   TurnStarted, RoundStarted, AssistantTextDelta, …
    agent.py        #   Tool Protocol + ToolRegistry dataclass
    session.py      #   Session: bundles Profile + Agent + PermissionEngine
                    #   + HookRegistry + ToolRegistry; one host-facing handle

  hooks/            # Cross-cutting observers / decision points
    registry.py     #   HookEvent, HookResult, HookRegistry — small pub/sub
    builtin/        #   The hooks codey ships with:
      permission.py #     PreToolUse → consults PermissionEngine
      audit_log.py  #     Pre+PostToolUse → ~/.cache/codey/calls.jsonl
      transcript.py #     Pre+PostToolUse → renders → / ← lines in UI
      stop_logger.py#     Stop → [turn finished: ...] meta line
      todo_nag.py   #     Reminds the model to plan after quiet rounds
      todo_render.py#     Renders the todo list into the UI
      subagent_render.py # Pre+PostToolUse → ⏵/⏷ meta lines for spawn_agent
      skill_render.py    # PostToolUse → ↳ meta line when load_skill returns
      otel.py       #     Opt-in OpenTelemetry tracing (--otel / CODEY_OTEL=1)

  permissions/      # "What is the agent allowed to do"
    rules.py        #   Mode, Rule, Verdict, BUILTIN_DENY/ALLOW
    engine.py       #   PermissionEngine + check() + workspace boundary
    io.py           #   ~/.config/codey/permissions.toml + ./.codey/...
    suggest.py      #   suggest_pattern() for the Remember modal

  tools/            # Pure capability functions. NO permission logic here —
                    # gating happens in the PreToolUse hook.
    bash.py read_file.py list_dir.py grep.py
    write_file.py apply_edit.py todo_write.py
    spawn_agent.py  #   Spawn an isolated sub-agent (depth-1 only).
    load_skill.py   #   Fetch one skill's body on demand.

  skills/           # SKILL.md loader (Claude-Code-compatible subset)
    models.py       #   Skill dataclass + Tier literal
    io.py           #   parse_skill_md() — frontmatter parser
    registry.py     #   SkillRegistry.scan/get/list_meta
  skills_bundled/   # Package-bundled default skills (empty in v1)

  config.py         # Profile loading from ~/.config/codey/config.toml
  prompt.py         # 4-layer system prompt: package default → user
                    # → ./codey.md → skills index (auto-injected when
                    # SkillRegistry is non-empty)
  prompts/system.md   # default system prompt (always loaded)
  prompts/subagent.md # default sub-agent system prompt (children only)

  ui/               # Textual full-screen UI (was tui.py)
    app.py          #   CodeyApp — compose, on_mount, key routing
    streaming.py    #   _stream_turn — consumes core.events, batches deltas
    slash_commands.py / slash_suggest.py
    renderers.py    #   _log_* helpers + UISinks
    modals/         #   approval.py, remember.py, profile_picker.py,
                    #   mode_picker.py, subagent_panel.py
```

### How a turn flows

```
user types →
  Agent.run() repairs history (drops orphan tool_calls from interrupted turns)
  → fires UserPromptSubmit hook
  → loops MAX_ROUNDS times:
      → calls the LLM (_stream_one_round, streams text + reassembles tool_calls)
      → if no tool_calls: break (natural stop)
      → dispatches ALL tool_calls of the round CONCURRENTLY via
        asyncio.gather; per-call hook order (PRE → request event → dispatch
        → result event → POST) is preserved inside each task; inter-call
        order is not. OpenAI matches tool responses by tool_call_id, not
        position, so concurrent history-append is wire-safe.
  → yields TurnCompleted
  → always fires Stop hook in finally
```

Cancellation (`esc` / `Ctrl-C`) and exceptions both roll history back to
the pre-turn baseline so the next request goes out clean.

### Sub-agents

The model can offload work to isolated sub-agents via the `spawn_agent` tool.
Each child gets a fresh context window, runs to completion, returns its final
assistant message as the tool result. Children inherit the parent's tools and
permissions, except they cannot themselves spawn further sub-agents and do
not have `todo_write` (the parent owns the plan). Multiple `spawn_agent`
calls in one assistant turn execute in parallel thanks to the concurrent
dispatch described above.

Key files:

  - `tools/spawn_agent.py`        — the model-facing tool; pure
  - `core/session.py:build_child_agent` — single place that constructs a
                                          child Agent (profile, hooks,
                                          tools, system prompt)
  - `core/session.py:SubAgentRecorder` — in-memory cap-bounded store of every
                                         event each child emitted; powers
                                         the `/subs` panel
  - `hooks/builtin/__init__.py:build_child_hooks` — curated hook registry
        for children (permission + audit only; no stop_logger, transcript,
        todo, OTel, or subagent_render)
  - `hooks/builtin/subagent_render.py` — emits ⏵ on PreToolUse and ⏷ on
        PostToolUse for every `spawn_agent` call on the parent's transcript
  - `prompts/subagent.md`         — default sub-agent system prompt
  - `ui/modals/subagent_panel.py` — `/subs` panel: snapshot-on-open
        listing of every child this session spawned + event timeline

Audit-log linkage: every line a child writes to `~/.cache/codey/calls.jsonl`
carries `parent_session_id`, so `jq 'select(.parent_session_id == "abc12345")'`
reconstructs the parent→child causal chain.

Approval modals triggered by a child include a `requester` line so the user
sees which sub-agent is asking (e.g. `sub-agent[2] "investigate-db"`).

### Skills

The model can extend itself at runtime by loading skills — markdown files
with YAML frontmatter, modeled on Claude Code's SKILL.md format. The
`name + description` of every discovered skill is in the system prompt
(via the 4th prompt layer); only the body is loaded on demand, when the
model calls the `load_skill` tool. This keeps a large library of procedures
available at near-zero token cost until one is actually needed.

Discovery: three tiers, scanned once at `Session.build`, project beats
user beats package:

  - `src/codey/skills_bundled/<name>/SKILL.md`  (package — ships with codey)
  - `~/.config/codey/skills/<name>/SKILL.md`     (user — applies everywhere)
  - `<workspace>/.codey/skills/<name>/SKILL.md`  (project — applies in this repo)

Override on name collision is silent — the winner-tier skill loads, the
loser tier writes a `skill_override` line to `~/.cache/codey/calls.jsonl`
(same audit log the audit_log hook uses). Malformed skills (missing
description, bad frontmatter, etc.) are skipped + logged with
`skill_invalid` — the scan never raises.

Key files:

  - `skills/registry.py`           — SkillRegistry.scan/get/list_meta
  - `skills/io.py`                 — parse_skill_md (no pyyaml dep)
  - `tools/load_skill.py`          — model-facing load tool
  - `hooks/builtin/skill_render.py` — `↳ skill loaded: <name>` meta line
  - `prompt.py:_skills_layer`      — 4th prompt layer (auto-skipped if empty)

Sub-agents inherit the same skill registry through the tool object and see
the same skills index in their system prompt — `load_skill` is one of the
tools children inherit by default.

### Event stream

`Agent.run()` is an `AsyncIterator[Event]`. Don't expose `print()` from the
agent — emit events (`AssistantTextDelta`, `ToolCallRequested`, `ToolResult`,
`TurnCompleted`, etc.) and let the UI render them. Hook callbacks may also
render (transcript hook does this) via UI-supplied writers.

---

## House rules (read before writing code)

### Don't break the test suite
`uv run pytest` is **the** verification. ~196 tests, runs in ~8 s. If you
break it, fix it before committing. New behavior gets a new test.

### Tools are pure
A `Tool` has four attributes: `name`, `description`, `parameters` (JSON
Schema), `async run(arguments)`. They don't take an `engine`, don't take an
`approve` callback, don't open modals, don't print. Permission gating is a
**hook** (see `hooks/builtin/permission.py`); rendering is a hook
(`hooks/builtin/transcript.py`). Adding a new tool is one file + one line
in `tools/__init__.py:build_default_registry`. See `README.md` § "Adding a
new tool" for the recipe.

(One narrow exception: `spawn_agent` accepts a `session_provider` callable
because it has to construct child `Agent`s — and that needs the live
Session for profile resolution, the shared engine, and the shared audit
writer. It's still pure in the contractual sense: no permission logic, no
UI rendering, returns a string. The wiring lives in `Session.build`, not
`build_default_registry`.)

### Tools return strings, not exceptions
On any failure (missing file, denied permission, timeout) a tool returns
`"error: …"`. Raising terminates the turn; returning lets the model react
(retry, ask the user, give up). Cap output size — tool results go into the
next prompt.

### Permission gating is the hook, not the tool
If you find yourself wanting to add an `engine` argument to a tool, stop.
Add the gating logic to the permission hook or add a new rule to the
built-in lists in `permissions/rules.py`. The whole point of the v2 hook
refactor was to make tools un-aware of permissions.

### History invariants
Every assistant message with `tool_calls` must be followed by a `role:"tool"`
message per call id. The OpenAI API 400s otherwise. `Agent._repair_history()`
fixes this at the top of each `run()`, so don't worry about it during normal
flow — but if you add a new code path that mutates `self.history` outside
`run()`, double-check.

Concurrent dispatch (`asyncio.gather` in `turn.py`) appends tool result
messages in nondeterministic order. That's fine because the API matches by
`tool_call_id`, not position. The PostToolUse-rewrite path captures
`idx = len(self.history)` *before* its append so a rewrite always lands on
the correct call's message even when other tasks append in parallel.

### Don't hardcode permission policy in tools
Built-in denylist / allowlist live in `permissions/rules.py`. User rules in
`~/.config/codey/permissions.toml`. Project rules in
`./.codey/permissions.toml`. There should never be `if command in
DESTRUCTIVE: ...` inside a tool's `run()`.

### Bash vs file-tool path matching
File tools (read_file/list_dir/grep/write_file/apply_edit) treat `~/foo` and
`/Users/me/foo` as equivalent in rule matching — that's
`_expand_for_path_tool` in `permissions/engine.py`. Bash matches literally because
shell expansion is the shell's job. Don't change this asymmetry without
reading the test in `test_permissions.py::test_path_normalization_does_not_affect_bash`.

### Don't write your own approval prompt
The UI provides `approve(ctx_dict) -> Verdict`. The permission hook calls it.
That's the only place that asks the user about a tool call. Don't add a
second prompt path.

### Streaming output buffer (TUI)
`ui/streaming.py:_stream_turn` accumulates `AssistantTextDelta` into the
app's `_assistant_buf` and flushes on `ToolCallRequested` /
`TurnCompleted`. If you change how text gets rendered, preserve this
batching — otherwise tool calls interleave with mid-sentence assistant
text and the transcript becomes unreadable.

### Don't write Markdown docs unless asked
This project has README.md and CLAUDE.md and that's the only documentation
that should exist. Don't create plan / design / summary files.

---

## Commit & PR conventions

- One logical change per commit.
- Commit message body explains **why**, not what.
- Always include `Co-Authored-By: <Model> <noreply@anthropic.com>` for AI
  contributions.
- All 196 tests must pass before committing.
- Use the existing 4-bucket style for big commits (see recent history):
  context → fix → behavior change → tests passing count.

## Where things go

| If you're adding… | Put it in |
|---|---|
| A new tool | `src/codey/tools/<name>.py` + register in `tools/__init__.py` |
| A new hook (observe/decide/rewrite at a known point) | `src/codey/hooks/builtin/<name>.py` + register in `hooks/builtin/__init__.py` |
| A new built-in permission rule | `BUILTIN_DENY` or `BUILTIN_ALLOW` in `permissions/rules.py` |
| A new built-in skill | `src/codey/skills_bundled/<name>/SKILL.md` (ships with the package) |
| A new slash command | `ui/slash_commands.py:_build_slash_commands()` |
| A new test | `tests/test_<area>.py`, async-style, using existing fixtures in `tests/conftest.py` |
| New CLI flag | `app.py:main()` argparse + thread through to `Session.build` / `CodeyApp` |

## Things NOT to do

- Don't reintroduce top-level `agent.py`, `tui.py`, `permissions.py`,
  `hooks.py`, or `builtin_hooks/`. They were intentionally split into
  `core/`, `ui/`, `permissions/`, and `hooks/` (see Architecture).
- Don't reintroduce `_gate.py`. Permission gating is a hook.
- Don't make `Agent` depend on `Profile` mutating — `Profile` is frozen.
  Use `Session.swap_profile()` (or `Agent.swap_profile()` directly) to switch.
- Don't add a `--no-permission-check` CLI flag. Use `--profile` + a
  yolo-mode permissions.toml, or `/permission mode yolo`.
- Don't bypass `uv` (no `pip install`, no `pip-tools`).
- Don't commit `~/.config/codey/` or `.codey/permissions.toml` from this
  repo — those contain user-specific approvals.

## Known things-in-flux

- `_assistant_buf` flushing logic in `ui/streaming.py` is brittle around
  mid-stream tool calls. Watch for visual glitches; fix by changing when
  we flush, not by changing event semantics.
- `Verdict` lives in `permissions/rules.py` (re-exported from
  `codey.permissions`) but is also re-exported from `tools/bash.py` for
  backward compat. The re-export can go away once we stop importing
  `Verdict` from `codey.tools`.
- Hook return type is `HookResult | None` everywhere. There's an open
  question about whether `str` should also be allowed as a shorthand for
  `HookResult(cancel=True, result=str)` — discussed but not implemented.
  Pick one if you touch the hook machinery.
- `HookResult.modified_post_result` is the only PostToolUse mutation
  channel and it bypasses the usual "hooks observe, don't decide"
  spirit of PostToolUse. Today it has exactly one user (the `todo_nag`
  hook, which appends a "plan first" reminder to the model-visible
  tool result). If a second use case appears, reconsider whether a
  full "tool result interceptor" abstraction is warranted instead.

## When in doubt

Read the test for the thing you're changing. Tests are the executable spec
of how each piece is supposed to behave. If the test doesn't exist, add one
before changing the code.
