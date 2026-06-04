# codey refactor plan — TUI-only + structural cleanup

**Status:** reviewed, decisions resolved
**Author:** drafted by Claude (Opus 4) on 2026-06-04, decisions resolved 2026-06-04
**Constraint:** no behavior change. Only structure, names, and file boundaries move.

This plan does two things in one coordinated pass:

1. **Deprecate the CLI front-end.** Hard delete `cli.py`, drop `prompt-toolkit`,
   remove the `--tui` flag, make the Textual UI the only mode.
2. **Restructure the package** so files map cleanly to concerns and `tui.py` /
   `agent.py` / `permissions.py` stop being grab-bags.

Existing functionality, persisted file paths, slash commands, hotkeys, modes,
rules, hooks, the audit-log format, and the visible TUI all stay identical.

---

## 1. What's wrong with the current shape

The current layout already separates the agent loop from tools, hooks, and
permissions — that's good and worth preserving. The remaining problems are:

| File | Lines | Smell |
|---|---|---|
| `src/codey/tui.py` | 868 | App + 4 modal screens + slash-suggest widget + slash-command registry + dispatch + key routing + event streaming + 12 render helpers, all in one file. Hard to navigate; hardest file in the repo to change confidently. |
| `src/codey/cli.py` | 537 | Entire second front-end maintained in parallel with the TUI. Each new slash command is implemented twice. To be deleted. |
| `src/codey/agent.py` | 442 | Mixes 5 concerns (message schema, event types, the turn loop, history repair, the streaming reassembler). `run()` is a 130-line procedure. |
| `src/codey/permissions.py` | 454 | Mixes the decision engine, the rule dataclasses + built-in lists, TOML I/O, and a pattern-suggestion helper. |
| Wiring duplication | — | `ConfigFile → Profile → PermissionEngine → ToolRegistry → build_default_hooks → Agent` is wired by hand in both `cli._run()` and `CodeyApp.on_mount`. Adding a new piece means editing both. |
| Top-level grouping | — | `agent.py`, `permissions.py`, `prompt.py`, `config.py`, `hooks.py`, `builtin_hooks/`, `tools/`, `prompts/`, `cli.py`, `tui.py` all sit at the same level. The conceptual layers (core / permissions / capabilities / observers / UI) aren't visible in the tree. |

Reference points considered:

- **claude-code** keeps event types in their own module and pushes a `Session`
  object as the single host-facing handle. We adopt both.
- **codex** (the Rust one) separates the provider client, the session state,
  and the UI into distinct crates. We do the *folder* equivalent (`core/`,
  `permissions/`, `ui/`) without introducing a provider abstraction (out of scope).
- **aider** keeps a `Coder` object that bundles model + tools + IO + history,
  similar to our proposed `Session`.

---

## 2. Target package layout

```
src/codey/
  __init__.py
  __main__.py              # NEW — `python -m codey` launches the TUI
  app.py                   # NEW — single entry point: argparse + Session.build + CodeyApp.run

  core/                    # provider-agnostic agent machinery
    __init__.py            # re-exports Agent, Message, all Event types, ToolRegistry, Tool
    agent.py               # façade re-exporting Agent from turn.py + the Tool/ToolRegistry types
    events.py              # TurnStarted / RoundStarted / AssistantTextDelta /
                           # AssistantMessageCompleted / ToolCallRequested /
                           # ToolResult / TurnCompleted / Event union
    messages.py            # Role, Message, Message.to_wire()
    history.py             # _repair_history(), reset helpers
    streaming.py           # _stream_one_round(), tool_call fragment reassembly, _RoundDone
    turn.py                # Agent class + run() — the orchestration body only
    session.py             # Session: bundles (Profile, Agent, PermissionEngine,
                           # HookRegistry, ToolRegistry, ConfigFile, workspace).
                           # One handle the UI talks to.

  permissions/             # "what is the agent allowed to do"
    __init__.py            # re-exports Mode, Rule, Allow/Deny/Ask, Verdict, PermissionEngine,
                           # suggest_pattern, MODE_DESCRIPTIONS
    rules.py               # Mode enum, Rule, Action, Allow/Deny/Ask, Decision, Verdict,
                           # BUILTIN_DENY, BUILTIN_ALLOW, READER_TOOLS, WRITER_TOOLS,
                           # PATH_TOOLS, MODE_DESCRIPTIONS
    engine.py              # PermissionEngine + check() + _inside_workspace()
    io.py                  # _load_file, _write_file, _toml_escape, USER_PERMISSIONS_PATH,
                           # PROJECT_PERMISSIONS_PATH
    suggest.py             # suggest_pattern()

  hooks/                   # cross-cutting observers / decision points
    __init__.py            # re-exports HookEvent, HookResult, HookRegistry, HookCallback, Hook
    registry.py            # was src/codey/hooks.py
    builtin/               # was src/codey/builtin_hooks/
      __init__.py          # build_default_hooks(...)
      permission.py
      transcript.py
      audit_log.py
      stop_logger.py
      todo_nag.py
      todo_render.py

  tools/                   # capability functions — unchanged structure
    __init__.py            # build_default_registry()
    bash.py
    read_file.py
    list_dir.py
    grep.py
    write_file.py
    apply_edit.py
    todo_write.py

  prompts/
    __init__.py
    system.md
  prompt.py                # build_system_prompt() — unchanged

  config.py                # unchanged

  ui/                      # was tui.py, split by concern
    __init__.py
    app.py                 # CodeyApp — compose, on_mount, on_input_*, on_key,
                           # action_*, busy state, worker lifecycle (~220 lines)
    streaming.py           # _stream_turn() — consumes core.events, batches text deltas
    slash_commands.py      # SlashCommand dataclass, _build_slash_commands(app),
                           # _handle_slash, substring resolver
    slash_suggest.py       # SlashSuggest widget + its CSS
    renderers.py           # _log_meta/_user/_assistant/_tool_call/_tool_result/_error,
                           # _make_tui_todo_writer, UISinks dataclass,
                           # the transcript_writer / meta_writer closures
                           # passed into build_default_hooks
    modals/
      __init__.py
      approval.py          # ApprovalScreen
      remember.py          # RememberScreen
      profile_picker.py    # ProfilePickerScreen
      mode_picker.py       # ModePickerScreen
```

**Top-level groupings** become five layers, visible in the tree:

- `core/` — agent loop, message/event types, session bundle
- `permissions/` — permission rules, engine, persistence
- `tools/` — pure capability functions (unchanged)
- `hooks/` — observers/decision points + the four built-in hooks
- `ui/` — Textual app, modals, slash commands, rendering

Plus the small glue at the package root: `app.py`, `__main__.py`, `config.py`,
`prompt.py`, `prompts/`.

---

## 3. The `Session` boundary

Both UI files today do the same wiring:

```python
cfg = ConfigFile.load()
profile = cfg.resolve(profile_arg)
workspace = Path.cwd().resolve()
engine = PermissionEngine.load(workspace=workspace)
tools = build_default_registry()
hooks = build_default_hooks(engine=engine, approve=…, transcript_writer=…,
                            meta_writer=…, todo_tool=…, todo_writer=…)
agent = Agent(profile=profile, system_prompt=build_system_prompt(),
              tools=tools, hooks=hooks)
```

Pull this into one class. The UI then talks to one object instead of five.

```python
# src/codey/core/session.py
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Session:
    profile: Profile
    workspace: Path
    cfg: ConfigFile
    agent: Agent
    engine: PermissionEngine
    hooks: HookRegistry
    tools: ToolRegistry

    @classmethod
    def build(
        cls,
        *,
        profile_arg: str | None,
        ui_sinks: "UISinks",          # from codey.ui.renderers
        workspace: Path | None = None,
    ) -> "Session":
        cfg = ConfigFile.load()
        profile = cfg.resolve(profile_arg)
        ws = (workspace or Path.cwd()).resolve()
        engine = PermissionEngine.load(workspace=ws)
        tools = build_default_registry()
        hooks = build_default_hooks(
            engine=engine,
            approve=ui_sinks.approve,
            transcript_writer=ui_sinks.transcript_writer,
            meta_writer=ui_sinks.meta_writer,
            todo_tool=tools.tools.get("todo_write"),
            todo_writer=ui_sinks.todo_writer,
        )
        agent = Agent(
            profile=profile,
            system_prompt=build_system_prompt(),
            tools=tools,
            hooks=hooks,
        )
        return cls(profile=profile, workspace=ws, cfg=cfg, agent=agent,
                   engine=engine, hooks=hooks, tools=tools)

    async def swap_profile(self, name: str) -> Profile:
        new_profile = self.cfg.resolve(name)
        await self.agent.swap_profile(new_profile)
        self.profile = new_profile
        return new_profile

    async def aclose(self) -> None:
        await self.agent.aclose()
```

```python
# src/codey/ui/renderers.py (excerpt)
from dataclasses import dataclass
from typing import Awaitable, Callable

@dataclass
class UISinks:
    transcript_writer: Callable[[str, str], None]   # (style, text)
    meta_writer:       Callable[[str], None]
    approve:           Callable[[dict], Awaitable["Verdict"]]
    todo_writer:       Callable[[list], None] | None
```

`CodeyApp.on_mount` shrinks from 30 lines of wiring to ~3 lines. Slash
commands read from `self.session.engine` / `self.session.cfg` / `self.session.agent`.

**No behavior change** — `Session.build()` is the same code, packaged.

---

## 4. Splitting `agent.py`

`agent.py` (442 lines) is read top-to-bottom but mixes 5 things. Split along
the existing comment boundaries:

| New file | Contents | Source lines |
|---|---|---|
| `core/events.py` | `TurnStarted`, `RoundStarted`, `AssistantTextDelta`, `AssistantMessageCompleted`, `ToolCallRequested`, `ToolResult`, `TurnCompleted`, `Event` union | agent.py:65-114 |
| `core/messages.py` | `Role`, `Message`, `Message.to_wire()` | agent.py:38-60 |
| `core/history.py` | `_repair_history` (free function taking history list), tested in isolation | agent.py:322-360 |
| `core/streaming.py` | `_stream_one_round`, `_RoundDone`, tool_call fragment reassembly | agent.py:398-442 |
| `core/turn.py` | `Agent` class + `run()` — calls `history.repair(...)` and `streaming.one_round(...)` instead of inlining them | agent.py:158-320 |
| `core/agent.py` | thin re-export: `from .turn import Agent`, plus the `Tool` Protocol and `ToolRegistry` dataclass | agent.py:117-156 |

`run()` itself **stays one function**. Its linear top-to-bottom story (repair
history → hook → loop → roll back on error → fire Stop) is the point. Splitting
it would hurt readability. But after lifting helpers out, `run()` drops from
~130 lines to ~80, and the helpers (`_repair_history`, `_stream_one_round`)
become independently testable instead of requiring an Agent instance.

Façade `src/codey/core/agent.py` keeps `from codey.core.agent import Agent`
working. A second façade `src/codey/agent.py` keeps the old import paths
alive for one cycle, then is removed in step 10.

---

## 5. Splitting `tui.py`

`tui.py` is 868 lines. After the split, **no file in `ui/` exceeds ~250 lines**.

| New file | Approx lines | Lifted from `tui.py` |
|---|---|---|
| `ui/modals/approval.py` | 45 | ApprovalScreen (lines 97-142) |
| `ui/modals/remember.py` | 48 | RememberScreen (lines 145-192) |
| `ui/modals/profile_picker.py` | 52 | ProfilePickerScreen (lines 196-247) |
| `ui/modals/mode_picker.py` | 52 | ModePickerScreen (lines 251-302) |
| `ui/slash_suggest.py` | 25 | SlashSuggest widget + CSS (lines 307-328) |
| `ui/slash_commands.py` | 90 | SlashCommand dataclass, `_build_slash_commands`, `_handle_slash`, substring resolver (lines 64-69, 472-491, 815-832) |
| `ui/renderers.py` | 90 | `_log_*` methods, `_make_tui_todo_writer`, UISinks dataclass, the closure factories that feed `build_default_hooks` (lines 72-92, 656-676, plus the closures from `on_mount` lines 399-410) |
| `ui/streaming.py` | 30 | `_stream_turn` (lines 836-863) |
| `ui/app.py` | 220 | `CodeyApp` itself — compose, on_mount, on_unmount, on_input_changed, on_input_submitted, on_option_list_option_selected, on_key, action_reset, action_profile, the `_cmd_*` slash handlers, the `_open_*_picker` helpers, busy state, worker lifecycle |

**Boundaries chosen so**:
- Each modal is one class with its own `DEFAULT_CSS`. Already independent in the source; the file split is mechanical.
- `slash_commands.py` exports `build_slash_commands(app) -> dict[str, SlashCommand]` and `handle_slash(app, line) -> Awaitable[None]`. The handlers themselves remain methods on `CodeyApp` (they read/write app state), but the registry construction and dispatch live separately.
- `renderers.py` owns every "write to RichLog" call site. The closures injected into `build_default_hooks` (`transcript_writer`, `meta_writer`, todo line writer) move here too — they're rendering, not orchestration.
- `streaming.py` owns the `agent.run()` consumer loop (text-delta batching, the `_assistant_buf` discipline noted in CLAUDE.md as "known-in-flux").
- `app.py` is left with orchestration: keystroke routing, worker management, lifecycle, and the `_cmd_*` slash handlers that mutate app state.

---

## 6. Splitting `permissions.py`

`permissions.py` (454 lines) splits cleanly:

| New file | Contents |
|---|---|
| `permissions/rules.py` | `Mode` enum, `MODE_DESCRIPTIONS`, `Action` literal, `Rule`, `Allow`, `Deny`, `Ask`, `Decision`, `Verdict`, `READER_TOOLS`, `WRITER_TOOLS`, `PATH_TOOLS`, `BUILTIN_DENY`, `BUILTIN_ALLOW` |
| `permissions/engine.py` | `PermissionEngine` class, `check()`, `_inside_workspace()`, the `_match`/`_first_match`/`_denies`/`_allows`/`_asks`/`_expand_for_path_tool` private helpers |
| `permissions/io.py` | `USER_PERMISSIONS_PATH`, `PROJECT_PERMISSIONS_PATH`, `_load_file`, `_write_file`, `_toml_escape` |
| `permissions/suggest.py` | `suggest_pattern()` |

`permissions/__init__.py` re-exports the public surface so call sites do
`from codey.permissions import Mode, PermissionEngine, Rule, Verdict, suggest_pattern, MODE_DESCRIPTIONS`.

---

## 7. Grouping `hooks/`

Lightest-touch part of the plan. No code changes inside hook files — just relocate:

```
src/codey/hooks.py                  →  src/codey/hooks/registry.py
src/codey/builtin_hooks/__init__.py →  src/codey/hooks/builtin/__init__.py
src/codey/builtin_hooks/*.py        →  src/codey/hooks/builtin/*.py
```

`hooks/__init__.py` re-exports `HookEvent`, `HookResult`, `HookRegistry`,
`HookCallback`, `Hook` from `.registry`. Imports inside the moved files change
from `from ..hooks import …` to `from ..registry import …` (one directory level
shallower).

---

## 8. CLI deletion in detail

**Files removed:**
- `src/codey/cli.py` (537 lines)
- `tests/test_cli_todo.py` (the only test that imports cli internals)

**`pyproject.toml` changes:**
- Drop `prompt-toolkit>=3.0.52` from `dependencies`.
- Change `[project.scripts] codey = "codey.cli:main"` → `codey = "codey.app:main"`.

**`src/codey/app.py` (new entry point):**

```python
"""Single entry point for codey. Launches the Textual UI."""
from __future__ import annotations
import argparse
from .ui.app import CodeyApp

def main() -> None:
    parser = argparse.ArgumentParser(prog="codey", description="codey — a coding agent")
    parser.add_argument("--profile", "-p",
                        help="profile from ~/.config/codey/config.toml")
    args = parser.parse_args()
    CodeyApp(profile_arg=args.profile).run()

if __name__ == "__main__":
    main()
```

**`src/codey/__main__.py` (new):**

```python
from .app import main
main()
```

So `python -m codey` works in addition to the installed `codey` script.

**Documentation:**
- README: remove "REPL (default)" section, the `--tui` flag, the table caption
  mentioning two modes. Update the project-layout block to match the new tree.
- CLAUDE.md: remove `--tui` references; update the "Where things go" table;
  update the architecture diagram to drop the `cli.py` row; replace
  `uv run codey` with the TUI-only invocation.

---

## 9. Migration sequence (each step keeps `uv run pytest` green)

Order is chosen so each step is small, locally testable, and reversible. Ships as **two PRs** to keep blast radius per merge small.

### PR-A — CLI deletion (1 commit, low risk)

1. **Delete the CLI.** Remove `src/codey/cli.py` and `tests/test_cli_todo.py`. Drop `prompt-toolkit` from `pyproject.toml`. Add a small `main()` wrapper inside `tui.py` (parses `-p/--profile`, calls `run(args.profile)`) and change the console script to `codey = "codey.tui:main"`. Update README to remove the REPL section, the `--tui` flag, and the two-mode table caption; update CLAUDE.md to remove `--tui` references and the cli.py row. **Gate:** ~124 tests pass.

Self-contained and reversible. Merge before starting PR-B.

### PR-B — Restructure (10 commits)

2. **Lift `events` + `messages` out of `agent.py`** into `core/events.py` and `core/messages.py`. Add re-exports in `agent.py`. **Gate:** green.

3. **Lift history + streaming helpers** into `core/history.py` and `core/streaming.py`. `Agent.run()` calls them. **Gate:** green.

4. **Move `agent.py` → `core/agent.py` + `core/turn.py`** with a top-level `src/codey/agent.py` shim that re-exports. **Gate:** green.

5. **Split `permissions.py` → `permissions/` package.** Because the new package name collides with the old module name, do this as one commit: (a) delete `src/codey/permissions.py`, (b) create `src/codey/permissions/` with `__init__.py` + `rules.py` + `engine.py` + `io.py` + `suggest.py`. The package's `__init__.py` re-exports the same public names the old module exposed, so every `from codey.permissions import …` call site (including all tests) keeps working unchanged. **Gate:** green.

6. **Move `hooks.py` + `builtin_hooks/` → `hooks/registry.py` + `hooks/builtin/`** with shims at the old paths. **Gate:** green.

7. **Split `tui.py` into `ui/` submodules.** Biggest single step — do it one file at a time (modals first, then slash machinery, then renderers, then streaming, then leave the slimmed `app.py`). Update `tests/test_tui.py` + `tests/test_tui_todo.py` imports (`from codey.ui.app import CodeyApp`, `from codey.ui.modals.approval import ApprovalScreen`, etc.). **Gate:** green.

8. **Introduce `Session`.** Add `core/session.py`. Rewrite `CodeyApp.on_mount` to call `Session.build(...)`. Migrate `swap_profile` calls through `Session`. Slash-command handlers read from `self.session.engine` / `.cfg` / `.agent`. **Gate:** green.

9. **Create `src/codey/app.py` + `__main__.py`.** Update `pyproject.toml` console script to `codey = "codey.app:main"`. `__main__.py` calls `app.main()` so `python -m codey` also works. **Gate:** green.

10. **Remove back-compat shims** from steps 4 and 6 (step 5 has no shim — the package's `__init__.py` IS the compatibility layer and stays). Grep for `from codey.agent`, `from codey.hooks` (without subpath), `from codey.builtin_hooks` outside the new façades and rewrite. **Gate:** green.

11. **Final docs pass.** Update CLAUDE.md "Where things go" table, the architecture diagram comment in `core/turn.py`, and the README project layout block to match the final tree. Commit.

Each step is one commit. Both PRs are bisectable.

---

## 10. Test impact

Existing test files and what changes in each:

| Test file | Change |
|---|---|
| `tests/test_agent_recovery.py` | imports stay (`from codey.agent import …`) thanks to step-4 façade; rewritten in step 10 to `from codey.core import …` |
| `tests/test_hooks.py` | same — `from codey.hooks import HookEvent, …` keeps working |
| `tests/test_permissions.py` | unchanged — `from codey.permissions import …` keeps working because the new `permissions/` package re-exports the same names |
| `tests/test_tools.py` | unchanged |
| `tests/test_tui.py` | step 7 rewrites imports: `from codey.ui.app import CodeyApp`, `from codey.ui.modals.approval import ApprovalScreen`, `from codey.ui.modals.profile_picker import ProfilePickerScreen`, `from codey.ui.slash_suggest import SlashSuggest` |
| `tests/test_tui_todo.py` | step 7 updates import to `from codey.ui.renderers import _make_tui_todo_writer` (or whatever its renamed equivalent is) |
| `tests/test_todo_nag.py` | unchanged (imports `from codey.builtin_hooks.todo_nag import …`); covered by the step-6 shim, then rewritten in step 10 to `from codey.hooks.builtin.todo_nag import …` |
| `tests/test_todo_render.py` | same as test_todo_nag.py |
| `tests/test_todo_write.py` | unchanged |
| `tests/test_cli_todo.py` | **deleted in step 1** |

Net: test count goes from ~126 to ~124. No test assertions change; only import
paths.

---

## 11. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Step 7 (TUI split) breaks a Pilot test because of CSS scoping when modal classes move | medium | Each modal already has its own `DEFAULT_CSS` attached to the class — moving the class moves its CSS. Run `pytest tests/test_tui.py` after each modal extraction, not just at end of step 7. |
| Step 8 (`Session`) breaks tests that read `app.engine` / `app.agent` / `app.hooks` directly | low | grep first; if matches exist, add `@property` shims on `CodeyApp` for one cycle (`@property def engine(self): return self.session.engine`). Remove in step 10. |
| External code importing `codey.cli`, `codey.permissions`, `codey.hooks` (module, not folder), `codey.builtin_hooks` breaks | low (personal project, no published package) | Shims from steps 4-6 give a release of overlap. Step 10 removes them. |
| The `agent.py` re-export façade creates a confusing two-file picture in `core/` | low | Step 10 removes the façade entirely. Final shape: only `core/turn.py` defines `Agent`; `core/__init__.py` re-exports it. |
| `prompt-toolkit` removal breaks a hidden import | very low | grep `prompt_toolkit` across the repo — only `cli.py` uses it. |
| Splitting `_stream_turn` away from `CodeyApp` makes the `_assistant_buf` batching ("known-in-flux" in CLAUDE.md) harder to reason about | medium | `ui/streaming.py` takes the app as an argument and writes through it; it doesn't own state. The buffer stays an attribute of `CodeyApp`. The split is just to isolate the event-consumption loop. |

---

## 12. Explicitly out of scope

These were considered and deliberately not included:

- **Provider abstraction.** No wrapper over `AsyncOpenAI`. Sticking with the OpenAI-compatible API surface keeps `streaming.py` trivial.
- **Event bus rewrite.** Hooks already are one. Don't reinvent.
- **Async-context redesign of hook callbacks.** Out of scope.
- **Tool-result interceptor abstraction.** CLAUDE.md flags `modified_post_result` as a question; leave it for a future PR with a second use case.
- **`Verdict` re-export cleanup from `tools.bash`.** CLAUDE.md flags this; separate small commit, not part of this refactor.
- **Schema changes** to `permissions.toml`, `config.toml`, or `system.md` layering.
- **New tests** beyond import-path updates.
- **A "Heavy" refactor path** (layer separation with provider plug-ins, typed event bus, codex-style crate boundaries). Considered, rejected: too much surface change for a personal learning project.

---

## 13. Functionality contract (what must NOT change)

- All slash commands behave identically: `/help`, `/exit`, `/reset`, `/model`, `/profiles`, `/profile [name]`, `/permission [status|list|mode <name>]`, `/hooks [enable|disable <name>]`.
- All hotkeys behave identically: `ctrl+c`, `ctrl+d`, `ctrl+r`, `ctrl+p`, `esc`.
- All four permission modes (`paranoid`, `read-only`, `safe`, `yolo`) behave identically; resolution order unchanged.
- Built-in deny + allow rule lists unchanged.
- All four built-in hooks (`permission`, `audit_log`, `transcript`, `stop_logger`) plus `todo_nag` + `todo_render` keep firing on the same events with the same outputs.
- Audit log JSONL format unchanged.
- Todo-list rendering unchanged (dim+strike for completed, bold for in_progress).
- Persisted file paths unchanged: `~/.config/codey/config.toml`, `~/.config/codey/permissions.toml`, `~/.config/codey/system.md`, `./.codey/permissions.toml`, `./codey.md`, `~/.cache/codey/calls.jsonl`.
- 3-layer system-prompt assembly (default + user + project) unchanged.
- All event types fire in the same order with the same payloads.

---

## 14. Estimated effort

- **Human, careful, one numbered step at a time:** ~1.5 dev days.
- **Agent, one numbered step per session, `uv run pytest` between each:** ~half day.

Each step is a single commit. Total: ~11 commits.

---

## 15. Resolved decisions

These were open questions during drafting; answered after review.

1. **Folder names.** `core / permissions / hooks / tools / ui`. Chose `permissions` over `policy` — more concrete and matches the existing module name (which becomes the package name in step 5, avoiding a rename in test imports). Considered `engine` (too overloaded), `runtime` (too vague), and `agent` (the folder also holds `Session` and message types, not just the loop) — all rejected.

2. **Session location.** `core/session.py`. Lives next to `Agent` which it wraps; the host imports it via `from codey.core import Session`.

3. **`UISinks` location.** `ui/renderers.py`. Sinks are UI concerns. `Session.build()` accepts them via structural typing (a `Protocol` defined in `core/session.py`) so `core/` doesn't need to import from `ui/`.

4. **Ship order.** CLI deletion ships as **PR-A** ahead of the restructure. Small, low risk, unblocks the rest. The 10-commit restructure ships as **PR-B**. See §9 for the split.

5. **`__main__.py`.** Yes — add it in step 9. Lets `python -m codey` work alongside the installed `codey` console script (the one generated by `pyproject.toml`'s `[project.scripts]` entry, which is what `uv run codey` invokes).
