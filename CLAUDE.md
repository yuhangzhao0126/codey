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
(bash, file read/write/grep, in-place edits) with a permission system. Two
front-ends: a `prompt_toolkit` line REPL (`codey`) and a Textual full-screen
TUI (`codey --tui`). Built as a learning project — small enough to read
end-to-end, big enough to be useful for day-to-day coding tasks.

Public surface for users is documented in `README.md`.

---

## Setup & common commands

```bash
uv sync                    # install deps into .venv
uv run pytest              # run the test suite (must stay green)
uv run codey               # REPL
uv run codey --tui         # Textual TUI
uv run codey -p deepseek   # pick a profile from ~/.config/codey/config.toml
```

Python **≥ 3.11** (uses `tomllib`). Use `uv add` / `uv remove` for deps;
`uv.lock` is committed.

---

## Architecture

The core data flow is one round of `Agent.run()`. Read `src/codey/agent.py`
top-to-bottom before changing any of it — the comments there are the source
of truth.

```
src/codey/
  agent.py          # Agent.run() = the turn loop. Streaming, multi-round
                    # tool dispatch, history repair, error rollback,
                    # cancellation. Fires hook events.
  hooks.py          # HookEvent, HookResult, HookRegistry — small pub/sub
  builtin_hooks/    # The four hooks codey ships with:
    permission.py   #   PreToolUse → consults PermissionEngine
    audit_log.py    #   Pre+PostToolUse → ~/.cache/codey/calls.jsonl
    transcript.py   #   Pre+PostToolUse → renders → / ← lines in UI
    stop_logger.py  #   Stop → [turn finished: ...] meta line
  permissions.py    # PermissionEngine + Mode + Rule. Built-in deny/allow
                    # lists, mode shortcuts (paranoid/read-only/safe/yolo),
                    # workspace boundary, TOML I/O.
  tools/            # Pure capability functions. NO permission logic here —
                    # gating happens in the PreToolUse hook.
    bash.py read_file.py list_dir.py grep.py
    write_file.py apply_edit.py
  config.py         # Profile loading from ~/.config/codey/config.toml
  prompt.py         # 3-layer system prompt: package default → user
                    # → ./codey.md (this file)
  cli.py            # prompt_toolkit REPL + slash commands
  tui.py            # Textual full-screen UI + slash commands + modals
  prompts/system.md # default system prompt (always loaded)
```

### How a turn flows

```
user types →
  Agent.run() repairs history (drops orphan tool_calls from interrupted turns)
  → fires UserPromptSubmit hook
  → loops MAX_ROUNDS times:
      → calls the LLM (_stream_one_round, streams text + reassembles tool_calls)
      → if no tool_calls: break (natural stop)
      → for each tool_call:
          → fires PreToolUse (permission hook may cancel / rewrite args)
          → dispatches tool via ToolRegistry
          → fires PostToolUse (audit, etc.)
  → yields TurnCompleted
  → always fires Stop hook in finally
```

Cancellation (`esc` / `Ctrl-C`) and exceptions both roll history back to
the pre-turn baseline so the next request goes out clean.

### Event stream

`Agent.run()` is an `AsyncIterator[Event]`. Don't expose `print()` from the
agent — emit events (`AssistantTextDelta`, `ToolCallRequested`, `ToolResult`,
`TurnCompleted`, etc.) and let the UI render them. Hook callbacks may also
render (transcript hook does this) via UI-supplied writers.

---

## House rules (read before writing code)

### Don't break the test suite
`uv run pytest` is **the** verification. ~126 tests, runs in ~5 s. If you
break it, fix it before committing. New behavior gets a new test.

### Tools are pure
A `Tool` has four attributes: `name`, `description`, `parameters` (JSON
Schema), `async run(arguments)`. They don't take an `engine`, don't take an
`approve` callback, don't open modals, don't print. Permission gating is a
**hook** (see `builtin_hooks/permission.py`); rendering is a hook
(`builtin_hooks/transcript.py`). Adding a new tool is one file + one line
in `tools/__init__.py:build_default_registry`. See `README.md` § "Adding a
new tool" for the recipe.

### Tools return strings, not exceptions
On any failure (missing file, denied permission, timeout) a tool returns
`"error: …"`. Raising terminates the turn; returning lets the model react
(retry, ask the user, give up). Cap output size — tool results go into the
next prompt.

### Permission gating is the hook, not the tool
If you find yourself wanting to add an `engine` argument to a tool, stop.
Add the gating logic to the permission hook or add a new rule to the
built-in lists in `permissions.py`. The whole point of the v2 hook
refactor was to make tools un-aware of permissions.

### History invariants
Every assistant message with `tool_calls` must be followed by a `role:"tool"`
message per call id. The OpenAI API 400s otherwise. `Agent._repair_history()`
fixes this at the top of each `run()`, so don't worry about it during normal
flow — but if you add a new code path that mutates `self.history` outside
`run()`, double-check.

### Don't hardcode permission policy in tools
Built-in denylist / allowlist live in `permissions.py`. User rules in
`~/.config/codey/permissions.toml`. Project rules in
`./.codey/permissions.toml`. There should never be `if command in
DESTRUCTIVE: ...` inside a tool's `run()`.

### Bash vs file-tool path matching
File tools (read_file/list_dir/grep/write_file/apply_edit) treat `~/foo` and
`/Users/me/foo` as equivalent in rule matching — that's
`_expand_for_path_tool` in `permissions.py`. Bash matches literally because
shell expansion is the shell's job. Don't change this asymmetry without
reading the test in `test_permissions.py::test_path_normalization_does_not_affect_bash`.

### Don't write your own approval prompt
The UI provides `approve(ctx_dict) -> Verdict`. The permission hook calls it.
That's the only place that asks the user about a tool call. Don't add a
second prompt path.

### Streaming output buffer (TUI)
`_stream_turn` accumulates `AssistantTextDelta` into `_assistant_buf` and
flushes on `ToolCallRequested` / `TurnCompleted`. If you change how text
gets rendered, preserve this batching — otherwise tool calls interleave
with mid-sentence assistant text and the transcript becomes unreadable.

### Lazy imports for heavy deps
`tui.py` is imported lazily in `cli.main()` only when `--tui` is passed.
Don't add top-level imports that pull in Textual from the REPL path.

### Don't write Markdown docs unless asked
This project has README.md and CLAUDE.md and that's the only documentation
that should exist. Don't create plan / design / summary files.

---

## Commit & PR conventions

- One logical change per commit.
- Commit message body explains **why**, not what.
- Always include `Co-Authored-By: <Model> <noreply@anthropic.com>` for AI
  contributions.
- All 126 tests must pass before committing.
- Use the existing 4-bucket style for big commits (see recent history):
  context → fix → behavior change → tests passing count.

## Where things go

| If you're adding… | Put it in |
|---|---|
| A new tool | `src/codey/tools/<name>.py` + register in `tools/__init__.py` |
| A new hook (observe/decide/rewrite at a known point) | `src/codey/builtin_hooks/<name>.py` + register in `builtin_hooks/__init__.py` |
| A new built-in permission rule | `BUILTIN_DENY` or `BUILTIN_ALLOW` in `permissions.py` |
| A new slash command | Same file as the others: `cli.py:_build_commands()` and `tui.py:_build_slash_commands()` (keep both in sync) |
| A new test | `tests/test_<area>.py`, async-style, using existing fixtures in `tests/conftest.py` |
| New CLI flag | `cli.py:main()` argparse + thread through to `_run` |

## Things NOT to do

- Don't add a top-level `tools.py` next to `agent.py` — tools live in
  `tools/`. Same for `hooks` vs `hooks/`.
- Don't reintroduce `_gate.py`. Permission gating is a hook.
- Don't make `Agent` depend on `Profile` mutating — `Profile` is frozen.
  Use `swap_profile()` to switch.
- Don't add a `--no-permission-check` CLI flag. Use `--profile` + a
  yolo-mode permissions.toml, or `/permission mode yolo`.
- Don't bypass `uv` (no `pip install`, no `pip-tools`).
- Don't commit `~/.config/codey/` or `.codey/permissions.toml` from this
  repo — those contain user-specific approvals.

## Known things-in-flux

- `_assistant_buf` flushing logic in TUI is brittle around mid-stream tool
  calls. Watch for visual glitches; fix by changing when we flush, not by
  changing event semantics.
- `Verdict` lives in `permissions.py` but is re-exported from
  `tools/bash.py` for backward compat. The re-export can go away once we
  stop importing `Verdict` from `codey.tools`.
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
