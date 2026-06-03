"""Permission engine: decides what the agent is allowed to do.

Concepts
--------
- **Mode**: one of paranoid / read-only / safe / yolo. The session-wide posture.
- **Rule**: `(tool, pattern, action, reason)`. `action` is allow | deny | ask.
- **Decision**: what the engine returns for a given tool call — Allow / Deny / Ask.

Resolution order (first match wins):

    1. Built-in deny rules                      # always enforced
    2. mode-specific shortcut:
         yolo      -> ALLOW (after built-in deny)
         paranoid  -> ASK   (after built-in deny)
    3. Project deny rules     (./.codey/permissions.toml)
    4. User deny rules        (~/.config/codey/permissions.toml)
    5. read-only mode shortcut: writers -> ASK
    6. Project allow rules
    7. User allow rules
    8. Project ask rules (user-attached reason)
    9. User ask rules
    10. Built-in allow rules
    11. Default by tool kind (reads -> ALLOW, writers -> ASK)

Pattern syntax: simple glob with `*`, matched against the tool's "canonical
arg string":
    bash       -> the literal `command` string
    read_file  -> the `path` argument
    list_dir   -> the `path` argument
    grep       -> the `path` argument
    write_file -> the `path` argument
    apply_edit -> the `path` argument
"""

from __future__ import annotations

import fnmatch
import os
import tomllib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal

USER_PERMISSIONS_PATH = Path.home() / ".config" / "codey" / "permissions.toml"
PROJECT_PERMISSIONS_PATH = Path(".codey") / "permissions.toml"


# ---------- types ----------

class Mode(str, Enum):
    PARANOID = "paranoid"
    READ_ONLY = "read-only"
    SAFE = "safe"
    YOLO = "yolo"


Action = Literal["allow", "deny", "ask"]


@dataclass(frozen=True)
class Rule:
    tool: str          # tool name, or "*" for any tool
    pattern: str       # glob
    action: Action
    reason: str = ""
    source: str = "builtin"   # "builtin" | "user" | "project"


@dataclass(frozen=True)
class Allow:
    reason: str = ""


@dataclass(frozen=True)
class Deny:
    reason: str
    rule: Rule | None = None  # which rule caused the deny (if any)


@dataclass(frozen=True)
class Ask:
    reason: str = ""
    rule: Rule | None = None


Decision = Allow | Deny | Ask


# ---------- which tools are "readers" vs "writers" ----------
# Mode behavior depends on knowing if a tool mutates state.
READER_TOOLS = frozenset({"read_file", "list_dir", "grep"})
WRITER_TOOLS = frozenset({"write_file", "apply_edit"})
# bash is special: handled by rules, not by classification.


# ---------- built-in rules ----------
# These ship with the package and CANNOT be removed by user config.
# Built-in *denies* override everything, including yolo mode.
# Built-in *allows* are checked LAST, so user rules can promote/demote them.

BUILTIN_DENY: list[Rule] = [
    Rule("bash", "rm -rf *",       "deny", "destructive recursive remove", "builtin"),
    Rule("bash", "rm -rf*",        "deny", "destructive recursive remove", "builtin"),
    Rule("bash", "rm -fr *",       "deny", "destructive recursive remove", "builtin"),
    Rule("bash", "rm -fr*",        "deny", "destructive recursive remove", "builtin"),
    Rule("bash", "mkfs*",          "deny", "filesystem format",            "builtin"),
    Rule("bash", "dd if=*",        "deny", "raw disk write",               "builtin"),
    Rule("bash", "* > /dev/sd*",   "deny", "raw device write",             "builtin"),
    Rule("bash", "* > /dev/disk*", "deny", "raw device write",             "builtin"),
    Rule("bash", "shutdown*",      "deny", "shutdown system",              "builtin"),
    Rule("bash", "reboot*",        "deny", "reboot system",                "builtin"),
    Rule("bash", "halt*",          "deny", "halt system",                  "builtin"),
    Rule("bash", "poweroff*",      "deny", "poweroff system",              "builtin"),
    Rule("bash", ":(){ :|:&*",     "deny", "fork bomb",                    "builtin"),
]

BUILTIN_ALLOW: list[Rule] = [
    # safe read-only bash commands
    Rule("bash", "ls",           "allow", "", "builtin"),
    Rule("bash", "ls *",         "allow", "", "builtin"),
    Rule("bash", "pwd",          "allow", "", "builtin"),
    Rule("bash", "cat *",        "allow", "", "builtin"),
    Rule("bash", "head *",       "allow", "", "builtin"),
    Rule("bash", "tail *",       "allow", "", "builtin"),
    Rule("bash", "wc *",         "allow", "", "builtin"),
    Rule("bash", "file *",       "allow", "", "builtin"),
    Rule("bash", "stat *",       "allow", "", "builtin"),
    Rule("bash", "find *",       "allow", "", "builtin"),
    Rule("bash", "which *",      "allow", "", "builtin"),
    Rule("bash", "whoami",       "allow", "", "builtin"),
    Rule("bash", "date",         "allow", "", "builtin"),
    Rule("bash", "uname*",       "allow", "", "builtin"),
    Rule("bash", "env",          "allow", "", "builtin"),
    Rule("bash", "printenv*",    "allow", "", "builtin"),
    Rule("bash", "echo *",       "allow", "", "builtin"),
    Rule("bash", "tree*",        "allow", "", "builtin"),
    # read-only git
    Rule("bash", "git status*",  "allow", "", "builtin"),
    Rule("bash", "git log*",     "allow", "", "builtin"),
    Rule("bash", "git diff*",    "allow", "", "builtin"),
    Rule("bash", "git show*",    "allow", "", "builtin"),
    Rule("bash", "git blame*",   "allow", "", "builtin"),
    Rule("bash", "git branch*",  "allow", "", "builtin"),
    Rule("bash", "git remote*",  "allow", "", "builtin"),
    Rule("bash", "git config*",  "allow", "", "builtin"),
]


# ---------- engine ----------

@dataclass
class PermissionEngine:
    """Holds the active mode plus all rules. Stateless beyond that — checks
    are pure functions of (tool, args, mode, rules)."""

    mode: Mode = Mode.SAFE
    user_rules: list[Rule] = field(default_factory=list)
    project_rules: list[Rule] = field(default_factory=list)

    # -- loading --

    @classmethod
    def load(
        cls,
        user_path: Path | None = None,
        project_path: Path | None = None,
    ) -> "PermissionEngine":
        """Read mode + rules from disk. Missing files are not errors."""
        user_path = user_path or USER_PERMISSIONS_PATH
        project_path = project_path or PROJECT_PERMISSIONS_PATH

        user_mode, user_rules = _load_file(user_path, source="user")
        _, project_rules = _load_file(project_path, source="project")
        mode = user_mode or Mode.SAFE
        return cls(mode=mode, user_rules=user_rules, project_rules=project_rules)

    def save_mode(self, mode: Mode, user_path: Path | None = None) -> None:
        """Persist a new mode into the user permissions file."""
        path = user_path or USER_PERMISSIONS_PATH
        self.mode = mode
        _write_file(path, mode=mode, rules=self.user_rules)

    def append_user_rule(self, rule: Rule, user_path: Path | None = None) -> None:
        path = user_path or USER_PERMISSIONS_PATH
        rule = Rule(rule.tool, rule.pattern, rule.action, rule.reason, source="user")
        self.user_rules.append(rule)
        _write_file(path, mode=self.mode, rules=self.user_rules)

    def append_project_rule(self, rule: Rule, project_path: Path | None = None) -> None:
        path = project_path or PROJECT_PERMISSIONS_PATH
        rule = Rule(rule.tool, rule.pattern, rule.action, rule.reason, source="project")
        self.project_rules.append(rule)
        # Project files don't store mode.
        _write_file(path, mode=None, rules=self.project_rules)

    def remove_user_rule_at(self, index: int, user_path: Path | None = None) -> Rule | None:
        if 0 <= index < len(self.user_rules):
            removed = self.user_rules.pop(index)
            _write_file(user_path or USER_PERMISSIONS_PATH,
                        mode=self.mode, rules=self.user_rules)
            return removed
        return None

    # -- decision --

    def check(self, tool: str, arg_str: str) -> Decision:
        """Return the access decision for `tool` invoked with the canonical
        `arg_str`. See module docstring for resolution order."""
        # 1. built-in deny
        if hit := _first_match(BUILTIN_DENY, tool, arg_str):
            return Deny(reason=hit.reason or "denied by built-in rule", rule=hit)

        # 2. mode shortcuts that bypass user rules
        if self.mode == Mode.YOLO:
            return Allow()
        if self.mode == Mode.PARANOID:
            return Ask(reason="paranoid mode asks for everything")

        # 3-4. project / user deny rules
        if hit := _first_match(_denies(self.project_rules), tool, arg_str):
            return Deny(reason=hit.reason or "denied by project rule", rule=hit)
        if hit := _first_match(_denies(self.user_rules), tool, arg_str):
            return Deny(reason=hit.reason or "denied by user rule", rule=hit)

        # 5. read-only mode: writers always ask
        if self.mode == Mode.READ_ONLY and tool in WRITER_TOOLS:
            return Ask(reason="read-only mode: writer tools require approval")
        if self.mode == Mode.READ_ONLY and tool == "bash":
            # bash can write; in read-only, require approval for anything not
            # already in the built-in allow set.
            if not _first_match(BUILTIN_ALLOW, tool, arg_str):
                return Ask(reason="read-only mode: bash requires approval")

        # 6-7. project / user allow rules
        if hit := _first_match(_allows(self.project_rules), tool, arg_str):
            return Allow(reason=hit.reason)
        if hit := _first_match(_allows(self.user_rules), tool, arg_str):
            return Allow(reason=hit.reason)

        # 8-9. project / user ask rules (attach reason to the prompt)
        if hit := _first_match(_asks(self.project_rules), tool, arg_str):
            return Ask(reason=hit.reason or "matched project ask rule", rule=hit)
        if hit := _first_match(_asks(self.user_rules), tool, arg_str):
            return Ask(reason=hit.reason or "matched user ask rule", rule=hit)

        # 10. built-in allow
        if hit := _first_match(BUILTIN_ALLOW, tool, arg_str):
            return Allow(reason=hit.reason)

        # 11. default by tool kind
        if tool in READER_TOOLS:
            return Allow()
        return Ask()


# ---------- helpers ----------

def _match(rule: Rule, tool: str, arg_str: str) -> bool:
    if rule.tool not in (tool, "*"):
        return False
    return fnmatch.fnmatchcase(arg_str, rule.pattern)


def _first_match(rules: Iterable[Rule], tool: str, arg_str: str) -> Rule | None:
    for rule in rules:
        if _match(rule, tool, arg_str):
            return rule
    return None


def _denies(rules: Iterable[Rule]) -> list[Rule]:
    return [r for r in rules if r.action == "deny"]


def _allows(rules: Iterable[Rule]) -> list[Rule]:
    return [r for r in rules if r.action == "allow"]


def _asks(rules: Iterable[Rule]) -> list[Rule]:
    return [r for r in rules if r.action == "ask"]


# ---------- TOML I/O ----------

def _load_file(path: Path, source: str) -> tuple[Mode | None, list[Rule]]:
    if not path.exists():
        return None, []
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except Exception:  # noqa: BLE001 — malformed config shouldn't kill the app
        return None, []

    mode_str = data.get("mode")
    mode: Mode | None = None
    if mode_str:
        try:
            mode = Mode(mode_str)
        except ValueError:
            mode = None

    raw_rules = data.get("rules") or []
    rules: list[Rule] = []
    for raw in raw_rules:
        try:
            rules.append(Rule(
                tool=str(raw["tool"]),
                pattern=str(raw["pattern"]),
                action=str(raw["action"]),  # type: ignore[arg-type]
                reason=str(raw.get("reason", "")),
                source=source,
            ))
        except KeyError:
            continue
    return mode, rules


def _write_file(path: Path, *, mode: Mode | None, rules: list[Rule]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if mode is not None:
        lines.append(f'mode = "{mode.value}"\n')
    for r in rules:
        lines.append("\n[[rules]]\n")
        lines.append(f'tool    = "{_toml_escape(r.tool)}"\n')
        lines.append(f'pattern = "{_toml_escape(r.pattern)}"\n')
        lines.append(f'action  = "{r.action}"\n')
        if r.reason:
            lines.append(f'reason  = "{_toml_escape(r.reason)}"\n')
    path.write_text("".join(lines), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------- pattern suggestion helper (for "always allow / deny" UX) ----------

def suggest_pattern(tool: str, arg_str: str) -> str:
    """Derive a reasonable starter pattern from a concrete command/path."""
    if tool != "bash":
        # For file tools, suggest a dir glob.
        path = Path(arg_str).expanduser()
        parent = str(path.parent)
        return f"{parent}/*" if parent not in ("", ".") else "*"
    # bash: keep the first 1-2 tokens, then *
    tokens = arg_str.split()
    if not tokens:
        return "*"
    if len(tokens) == 1:
        return f"{tokens[0]}*"
    # `git status -sb` -> `git status*`; `npm test --watch` -> `npm test*`
    return f"{tokens[0]} {tokens[1]}*"
