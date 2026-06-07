# Skills for codey — design

Adds a Claude-Code-style skill system to codey: each skill is a directory with
a `SKILL.md`. Only the `name + description` of every skill is in the system
prompt by default; bodies are loaded on demand by a `load_skill` tool the
model calls when it decides a skill is relevant. The point is to make a large
library of procedures available without paying their token cost up front.

## Goals & non-goals

**Goals**
- Discover skills from three tiers (package-bundled, user, project) with
  predictable override semantics.
- Surface `name + description` for every skill in the system prompt.
- Let the model load any one skill's body on demand via a tool call.
- Make sub-agents see the same index and have the same tool.
- Stay compatible with Claude Code's directory layout so existing SKILL.md
  files work in codey unchanged.

**Non-goals (v1)**
- Filesystem watching / live reload.
- A `/skills reload` slash command.
- Full Claude Code frontmatter (`allowed-tools`, `disable-model-invocation`,
  `$ARGUMENTS` substitution, `` !`command` `` injection, etc.).
- User-invocable `/<skill-name>` slash commands.
- Supporting-file conventions inside a skill directory beyond `SKILL.md`
  (they're allowed on disk, just not parsed or referenced by the loader).

## Format — a strict subset of Claude Code's SKILL.md

A skill is a directory whose name is the skill's identifier, containing a
file `SKILL.md` with this shape:

```markdown
---
name: brainstorming
description: Use this before any creative work — explores intent and requirements before implementation.
---

# Brainstorming

…markdown body…
```

- Opens with `---\n`, lists `key: value` lines until a closing `---\n`, then
  the body.
- Recognized frontmatter keys: `description` (required) and `name` (optional).
  Unknown keys are ignored with an audit-log warning so SKILL.md files
  authored for Claude Code load without modification.
- The skill's identifier is the **directory name**. If `name:` is present in
  frontmatter it must equal the directory name (consistency check — see
  validation table).
- No size limit on the body.

## Discovery — three tiers, project wins

| Tier | Path |
|---|---|
| Package-bundled | `src/codey/skills_bundled/<name>/SKILL.md` (loaded via `importlib.resources`) |
| User | `~/.config/codey/skills/<name>/SKILL.md` |
| Project | `<workspace>/.codey/skills/<name>/SKILL.md` |

Override precedence on name collision: **project > user > package** (most
specific wins, same as `codey.md` over `system.md`).

Override is silent at the user level — the loaded skill simply comes from
the winning tier. Every override writes one JSONL line to the same audit
log the `audit_log` hook already uses (`~/.cache/codey/calls.jsonl`), so
there's one unified record:

```json
{"event": "skill_override", "name": "...", "winner_tier": "project",
 "winner_path": "...", "loser_tier": "user", "loser_path": "..."}
```

The package directory `skills_bundled/` (rather than `skills/`) avoids a
collision with the `codey.skills` Python module.

## Module layout

```
src/codey/
  skills/
    __init__.py        # re-exports: Skill, SkillRegistry, build_default_registry
    models.py          # @dataclass Skill { name, description, body, source_path, tier }
    io.py              # _parse_skill_md() — frontmatter parser, no pyyaml dep
    registry.py        # SkillRegistry: scan(), get(), names(), list_meta()
  skills_bundled/      # package-bundled defaults (empty in v1, dir + .keepdir)
  tools/
    load_skill.py      # LoadSkillTool — model-facing tool
  prompt.py            # gets a `skills_index` 4th layer
```

`Skill.source_path` and `Skill.tier` (`"package" | "user" | "project"`)
are kept on the dataclass so the audit log can record where an override
came from.

Frontmatter parsing is ~15 lines in `io.py` (key/value lines only — no
nested structures, no lists). Avoiding a `pyyaml` dependency keeps the
project's dep tree small.

## Data flow

### At session build

```
Session.build()
  ├─ registry = SkillRegistry.scan(workspace=ws, audit_writer=...)
  │     ├─ scan src/codey/skills_bundled/   # package tier
  │     ├─ scan ~/.config/codey/skills/      # user tier (overrides package)
  │     ├─ scan ws/.codey/skills/            # project tier (overrides both)
  │     └─ for each override: audit_writer({"event": "skill_override", ...})
  ├─ agent  = Agent(system_prompt=build_system_prompt(skills=registry), ...)
  └─ tools.register(LoadSkillTool(skills=registry))
```

### At prompt assembly

`build_system_prompt(cwd, skills)` becomes a 4-layer concat:

```
default + user + project + skills_index
```

`build_subagent_system_prompt(...)` gets the same `skills_index` layer
appended after its existing layers, so children see the same index and can
load skills on their own.

`SkillRegistry.list_meta()` returns the index text:

```
## Available skills

You can load any of these on demand by calling `load_skill` with the skill's name.
Each skill's body is hidden until you load it — load only what's relevant to the
current task.

- **brainstorming** — Use this before any creative work — explores intent and requirements before implementation.
- **systematic-debugging** — Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes.
- **test-driven-development** — Use when implementing any feature or bugfix, before writing implementation code.
```

If the registry is empty, `list_meta()` returns `""` and the layer is
skipped silently (same rule as missing user/project layers today).

### At runtime — model calls `load_skill`

```
LoadSkillTool.run({"name": "brainstorming"})
  ├─ skill = self.skills.get("brainstorming")   # O(1), body already in memory
  ├─ hit  → return skill.body                   # frontmatter stripped at scan time
  └─ miss → return f"error: no skill named 'brainstorming'. Available: ..."
```

No disk I/O at call time. Permission gating: trivially allowed (in-memory
read, like `todo_write`).

## Validation rules

All failures are soft-fail: the offending skill is skipped, one JSONL line
is written to the audit log, and the scan continues.

| Failure | Audit `reason` |
|---|---|
| Skill directory has no `SKILL.md` | `"no SKILL.md"` |
| Missing opening `---` line | `"no frontmatter"` |
| Missing closing `---` line | `"unclosed frontmatter"` |
| Missing `description` field | `"missing description"` |
| `name:` field present but ≠ directory name | `"name mismatch"` |
| Body is empty | `"empty body"` |
| Name collides with already-loaded skill of the same tier | `"duplicate name"` |
| File not valid UTF-8 | `"not utf-8"` |

The audit-log entry shape:

```json
{"event": "skill_invalid", "path": "...", "tier": "user", "reason": "missing description"}
```

## `LoadSkillTool` contract

```python
@dataclass
class LoadSkillTool:
    skills: SkillRegistry = None   # injected at construction

    name: str = "load_skill"
    description: str = (
        "Load the full body of a named skill. The skill index in your system "
        "prompt lists every available skill with a short description; this "
        "tool fetches the body of one when you decide to use it. Pass the "
        "skill name (matches the bullet in the index). Returns the skill's "
        "markdown body. Returns 'error: ...' if no such skill exists."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill name as shown in the index.",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }

    async def run(self, arguments: dict) -> str:
        name = (arguments.get("name") or "").strip()
        if not name:
            return "error: empty skill name"
        skill = self.skills.get(name)
        if skill is None:
            available = ", ".join(sorted(self.skills.names())) or "(none)"
            return f"error: no skill named {name!r}. Available: {available}"
        return skill.body
```

**Registration.** The tool needs a registry instance, so it follows the
same pattern as `SpawnAgentTool`: registered from `Session.build()` after
the registry is constructed, not from `build_default_registry()`. One line
added at the end of `Session.build()`:

```python
tools.register(LoadSkillTool(skills=registry))
```

**Sub-agent inheritance.** `build_child_agent` already copies the parent's
tool registry minus `{spawn_agent, todo_write}`. `load_skill` is not in
that exclusion set, so children inherit it. The same registry instance is
shared via the tool object — no extra wiring.

## Caching & scan timing

`SkillRegistry.scan()` runs once, inside `Session.build()`. Skills are
frozen for the session's lifetime. Mirrors `PermissionEngine.load()` and
`build_system_prompt()`, which also read once at startup. No filesystem
watcher, no reload command (both deferred — see Non-goals).

Within a turn, repeated `load_skill("foo")` calls return the same cached
body — no re-reads. The registry holds bodies in memory from the initial
scan.

## Testing

New `tests/test_skills.py`. Plumbing uses `tmp_path` + monkeypatched scan
roots, mirroring `test_permissions.py` and `test_prompt.py`.

| Test | Verifies |
|---|---|
| `test_scan_finds_package_skill` | scaffold a fake `skills_bundled/foo/SKILL.md`; assert it's in the registry |
| `test_user_overrides_package` | same name in user dir wins; one `skill_override` audit entry written |
| `test_project_overrides_user` | three-tier precedence: project beats user beats package |
| `test_missing_description_skipped` | malformed SKILL.md → logged + skipped, scan continues |
| `test_name_mismatch_skipped` | `name:` field ≠ dirname → skipped with `"name mismatch"` |
| `test_list_meta_format` | output matches the index format above |
| `test_list_meta_empty_when_no_skills` | empty registry → empty string so the layer drops silently |
| `test_load_skill_tool_returns_body` | tool returns body with frontmatter stripped |
| `test_load_skill_tool_unknown_name` | error string includes `Available: ...` |
| `test_subagent_inherits_skills` | child's system prompt includes the index; child's tool registry includes `load_skill` |

**Manual smoke test** before merging: copy a real SKILL.md (e.g. one of the
superpowers skills) into `~/.config/codey/skills/<name>/`, run
`uv run codey`, confirm `load_skill` works.

## Reference — what we borrowed from Claude Code

- Directory-per-skill layout with `SKILL.md` entrypoint.
- YAML-flavored frontmatter delimited by `---`.
- `description` as the load-decision signal in the index.
- Directory name (not frontmatter `name:`) as the canonical identifier.
- Three-tier discovery with most-specific-wins precedence.

What we deliberately left out for v1 — and why:

- `allowed-tools` / `disallowed-tools` — codey's permissions live in
  `permissions/`, not on individual skills. Adding skill-scoped tool
  filtering would mean a new dimension in the permission engine. Out of
  scope.
- `disable-model-invocation` / `user-invocable` — codey has no slash-command
  invocation for skills in v1, so neither flag has anything to gate.
- `$ARGUMENTS` substitution and `` !`command` `` injection — these are
  templating features. Useful but orthogonal to "load on demand", and each
  carries its own design surface (argument parsing, shell-exec semantics,
  audit-log shape for injected output). Each can be added independently
  later without breaking the v1 format.
- Filesystem watching / live reload — real complexity (new dep, background
  task lifecycle, races with concurrent dispatch). YAGNI for a learning
  project; restart-to-pick-up-changes is fine.
