# codey

A minimal terminal-based coding agent. Talks to any OpenAI-compatible API,
runs tools on your machine (shell, file read/write/search, in-place edits),
in a Textual full-screen UI.

Built as a learning project — small enough to read end-to-end (~1.5k LOC),
big enough to be genuinely useful for day-to-day coding tasks.

```
┌── codey ─── deepseek · deepseek-v4-pro · https://api.deepseek.com/v1 ──┐
│                                                                        │
│ you  › what python version does this project require?                  │
│   → bash(command='grep python pyproject.toml')                         │
│   ← bash [ok]                                                          │
│       requires-python = ">=3.11"                                       │
│ codey› This project requires Python 3.11 or higher.                    │
│                                                                        │
│ > █                                                                    │
│ ctrl+c quit · ctrl+r reset · ctrl+p provider                            │
└────────────────────────────────────────────────────────────────────────┘
```

## What codey can do

- **Chat** with any OpenAI-compatible model (OpenAI, DeepSeek, Anthropic via
  proxy, local servers like Agent Maestro / Ollama / vLLM).
- **Run tools** the model asks for, with safety prompts where they matter:
  - `bash` — shell commands; read-only commands (`ls`, `cat`, `git status` …)
    auto-run, anything else asks for approval
  - `read_file` — UTF-8 file contents
  - `list_dir` — structured directory listing
  - `grep` — regex search across files (skips `.git`, `.venv`, etc.)
  - `write_file` — create/overwrite a file (asks first)
  - `apply_edit` — aider-style search/replace block edits (asks first)
- **Switch providers/models on the fly** via providers in `~/.config/codey/config.toml`,
  with `/provider [name]` or `ctrl+p` for an inline picker.
- **Stream responses** so you see output as the model produces it.
- **Cancel a runaway turn** with `esc` in the TUI.

## Install

One line — installs `uv` if needed, then codey from the latest `main`. Re-run
to update; it seeds a placeholder config and never overwrites your own:

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/yuhangzhao0126/codey/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/yuhangzhao0126/codey/main/install.ps1 | iex"
```

Then edit your config (`~/.config/codey/config.toml`, or `%USERPROFILE%\.config\codey\config.toml`
on Windows) to add your API key and run `codey`.

## Setup (from source)

Requirements: **Python ≥ 3.11** and [**uv**](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/yuhangzhao0126/codey.git
cd codey
uv sync                                  # creates .venv, installs deps
```

### Configure a provider

Create `~/.config/codey/config.toml` (codey will bootstrap one from `.env`
on first run if it doesn't exist, but writing it directly is simpler):

```toml
default_provider = "openai"

[providers.openai]
base_url = "https://api.openai.com/v1"
api_key  = "sk-..."
model    = "gpt-4o-mini"

[providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key  = "sk-..."
model    = "deepseek-chat"

# Local / OpenAI-compatible server (e.g. Ollama, vLLM, Agent Maestro)
[providers.local]
base_url = "http://localhost:11434/v1"
api_key  = "sk-local"
model    = "llama3.1"
```

Each provider is a self-contained `(base_url, api_key, model)` bundle.

## Run

```bash
uv run codey                       # use default_provider
uv run codey --provider deepseek           # pick a provider for this run
uv run codey --otel                # also emit OpenTelemetry spans
                                   #   (requires: uv sync --extra observability)
```

### Slash commands

| Command           | Description                                                            |
|-------------------|------------------------------------------------------------------------|
| `/help`           | show all commands                                                      |
| `/exit`           | quit                                                                   |
| `/reset`          | clear chat history (keeps system prompt)                               |
| `/model`          | show the active provider / model / base_url                             |
| `/providers`       | list available providers                                                |
| `/provider`        | open the inline arrow-key picker                                       |
| `/provider <name>` | switch directly to a provider                                           |

Commands are **searchable** — type `/pro` to see a dropdown of matches; pick
one with ↑/↓ + Enter.

### Hotkeys

| Key       | Action                                       |
|-----------|----------------------------------------------|
| `ctrl+c`  | quit                                         |
| `ctrl+r`  | clear history                                |
| `ctrl+p`  | open the provider picker                      |
| `esc`     | close the slash-command dropdown, OR cancel an in-flight model turn |

## System prompt customization

codey's prompt is built by **appending** three layers (all optional except the
default):

1. **Default** — ships with the package
2. **User** — `~/.config/codey/system.md`
3. **Project** — `./codey.md` in the directory where you run codey

This works exactly like CLAUDE.md: the agent's core identity stays constant
and each project layers on its own context (e.g. "this repo uses pnpm not npm").

## Adding a new tool

Drop a class in `src/codey/tools/<your_tool>.py` that satisfies the `Tool`
Protocol (four attributes: `name`, `description`, `parameters`, async `run`),
then register it with one line in `src/codey/tools/__init__.py`:

```python
reg.register(YourTool())
```

The agent loop and both UIs need no changes. See `tools/read_file.py` for the
simplest example or `tools/bash.py` for the approval-aware pattern.

## Debugging

The TUI deliberately hides per-call `→ tool(...)` / `← tool [ok]` lines so the
conversation reads cleanly. To see what tools are running, you have two options:

**Tail the audit log** — every tool call is recorded as one line of JSON:

```bash
tail -f ~/.cache/codey/calls.jsonl | jq .
# Filter to one session:
jq 'select(.session_id == "8f3a2b")' ~/.cache/codey/calls.jsonl
```

**Attach an OpenTelemetry viewer** — `--otel` emits spans for every turn and
nested tool call. First install the optional extra:

```bash
uv sync --extra observability
```

Point at any OTel collector (Jaeger, Tempo, Honeycomb, Datadog, …) by setting
`OTEL_EXPORTER_OTLP_ENDPOINT`. For local LLM-aware tracing, [Arize Phoenix](https://github.com/Arize-ai/phoenix)
runs in one container:

```bash
docker run -p 6006:6006 -p 4318:4318 arizephoenix/phoenix:latest
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run codey --otel
# open http://localhost:6006
```

`CODEY_OTEL=1` and a `[otel] enabled = true` block in `~/.config/codey/config.toml`
also turn it on, so you can leave the flag off and keep tracing always-on.

## Project layout

```
src/codey/
  app.py         # entry point: argparse + CodeyApp.run()
  __main__.py    # `python -m codey`
  config.py      # provider loading from ~/.config/codey/config.toml
  prompt.py      # 3-layer system prompt assembly
  prompts/
    system.md    # default system prompt
  core/          # agent loop + Session + message/event types
    turn.py streaming.py history.py messages.py events.py
    agent.py session.py
  hooks/         # pub/sub registry + built-in hooks
    registry.py
    builtin/     # permission, audit_log, transcript, stop_logger,
                 # todo_nag, todo_render
  permissions/   # rules, engine, TOML I/O, suggest_pattern
  tools/         # bash, read_file, list_dir, grep, write_file,
                 # apply_edit, todo_write
  ui/            # Textual app + modals + slash commands + renderers
    app.py streaming.py renderers.py
    slash_commands.py slash_suggest.py
    modals/      # approval, remember, provider_picker, mode_picker
tests/           # pytest + Textual Pilot integration tests
```

## Tests

```bash
uv run pytest
```

153 tests across the agent, tools, and TUI. The TUI tests run headlessly via
Textual's `Pilot` — no real terminal needed.

## License

Personal project, no formal license. Read it, learn from it, fork it.
