"""Rules, modes, and decision dataclasses for the permission engine.

Pure data — no I/O, no engine logic. Imported by both the engine and the
permission hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Mode(str, Enum):
    PARANOID = "paranoid"
    READ_ONLY = "read-only"
    SAFE = "safe"
    YOLO = "yolo"


MODE_DESCRIPTIONS: dict[Mode, str] = {
    Mode.PARANOID:  "ask for every tool call (built-in deny still wins)",
    Mode.READ_ONLY: "auto-allow readers; writers and unknown bash always ask",
    Mode.SAFE:      "default — built-in deny/allow rules, unknowns ask",
    Mode.YOLO:      "no prompts (built-in deny still wins) — careful!",
}


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


@dataclass(frozen=True)
class Verdict:
    """Result of a user-facing approval prompt.

    Returned by the host's `approve` callback when the engine asks for user
    consent. `remember=True` tells the permission hook to append a rule to
    the engine's user/project store so the next matching call won't prompt.
    """
    allowed: bool
    remember: bool = False
    remember_action: str = "allow"        # "allow" | "deny"
    remember_pattern: str = ""
    remember_scope: str = "project"       # "project" | "user"


# ---------- which tools are "readers" vs "writers" ----------
# Mode behavior depends on knowing if a tool mutates state.
READER_TOOLS = frozenset({"read_file", "list_dir", "grep"})
WRITER_TOOLS = frozenset({"write_file", "apply_edit"})
# bash is special: handled by rules, not by classification.

# Tools whose canonical arg_str is a filesystem path. For these we expand `~`
# on both sides during matching so rules saved with one form (e.g.
# `/Users/me/Desktop/*`) match calls in the other form (e.g. `~/Desktop/foo`)
# and vice versa.
PATH_TOOLS = READER_TOOLS | WRITER_TOOLS


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
    # in-memory state — no filesystem / shell access
    Rule("todo_write", "*",      "allow", "in-memory task list", "builtin"),
]
