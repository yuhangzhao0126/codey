"""Permission engine: decides what the agent is allowed to do.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .io import (
    PROJECT_PERMISSIONS_PATH,
    USER_PERMISSIONS_PATH,
    load_file,
    write_file,
)
from . import io as _io_mod
from .rules import (
    BUILTIN_ALLOW,
    BUILTIN_DENY,
    PATH_TOOLS,
    WRITER_TOOLS,
    Allow,
    Ask,
    Decision,
    Deny,
    Mode,
    Rule,
)


@dataclass
class PermissionEngine:
    """Holds the active mode plus all rules. Stateless beyond that — checks
    are pure functions of (tool, args, mode, rules, workspace).

    `workspace` is the trust boundary for file-tool calls. When set, any
    read_file/list_dir/grep/write_file/apply_edit call whose target path
    resolves outside this directory requires user approval (overridable by
    a user/project allow rule, bypassed entirely in yolo mode).
    """

    mode: Mode = Mode.SAFE
    user_rules: list[Rule] = field(default_factory=list)
    project_rules: list[Rule] = field(default_factory=list)
    workspace: Path | None = None

    # -- loading --

    @classmethod
    def load(
        cls,
        user_path: Path | None = None,
        project_path: Path | None = None,
        workspace: Path | None = None,
    ) -> "PermissionEngine":
        """Read mode + rules from disk. Missing files are not errors."""
        user_path = user_path or _user_path()
        project_path = project_path or _project_path()

        user_mode, user_rules = load_file(user_path, source="user")
        _, project_rules = load_file(project_path, source="project")
        mode = user_mode or Mode.SAFE
        return cls(
            mode=mode,
            user_rules=user_rules,
            project_rules=project_rules,
            workspace=workspace,
        )

    def save_mode(self, mode: Mode, user_path: Path | None = None) -> None:
        """Persist a new mode into the user permissions file."""
        path = user_path or _user_path()
        self.mode = mode
        write_file(path, mode=mode, rules=self.user_rules)

    def append_user_rule(self, rule: Rule, user_path: Path | None = None) -> None:
        path = user_path or _user_path()
        rule = Rule(rule.tool, rule.pattern, rule.action, rule.reason, source="user")
        self.user_rules.append(rule)
        write_file(path, mode=self.mode, rules=self.user_rules)

    def append_project_rule(self, rule: Rule, project_path: Path | None = None) -> None:
        path = project_path or _project_path()
        rule = Rule(rule.tool, rule.pattern, rule.action, rule.reason, source="project")
        self.project_rules.append(rule)
        # Project files don't store mode.
        write_file(path, mode=None, rules=self.project_rules)

    def remove_user_rule_at(self, index: int, user_path: Path | None = None) -> Rule | None:
        if 0 <= index < len(self.user_rules):
            removed = self.user_rules.pop(index)
            write_file(user_path or _user_path(),
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

        # Built-in tool with no filesystem / shell impact — always allow,
        # before any mode shortcut. Keeps planning calls invisible to the
        # user even in paranoid mode.
        if tool == "todo_write":
            return Allow(reason="in-memory task list")

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

        # 6. workspace boundary for path tools.
        # If the resolved target is outside the workspace, we want to ASK —
        # but allow rules (project then user) can override the boundary, so
        # the user can grant "always allow /etc/hosts" via the approval modal.
        outside_reason: str | None = None
        if (
            self.workspace is not None
            and tool in PATH_TOOLS
            and not self._inside_workspace(arg_str)
        ):
            outside_reason = f"path is outside the workspace ({self.workspace})"

        # 7. project / user allow rules
        if hit := _first_match(_allows(self.project_rules), tool, arg_str):
            return Allow(reason=hit.reason)
        if hit := _first_match(_allows(self.user_rules), tool, arg_str):
            return Allow(reason=hit.reason)

        # 8. workspace ASK takes effect now (no allow rule rescued it).
        if outside_reason is not None:
            return Ask(reason=outside_reason)

        # 9-10. project / user ask rules (attach reason to the prompt)
        if hit := _first_match(_asks(self.project_rules), tool, arg_str):
            return Ask(reason=hit.reason or "matched project ask rule", rule=hit)
        if hit := _first_match(_asks(self.user_rules), tool, arg_str):
            return Ask(reason=hit.reason or "matched user ask rule", rule=hit)

        # 11. built-in allow
        if hit := _first_match(BUILTIN_ALLOW, tool, arg_str):
            return Allow(reason=hit.reason)

        # 12. default by tool kind
        from .rules import READER_TOOLS
        if tool in READER_TOOLS:
            return Allow()
        return Ask()

    def _inside_workspace(self, arg_str: str) -> bool:
        """Resolve `arg_str` (a path) and check it's at or below self.workspace.
        Resolution follows symlinks so a link inside the workspace pointing to
        /etc is correctly treated as outside."""
        if self.workspace is None:
            return True
        try:
            resolved = Path(arg_str).expanduser().resolve()
            workspace = self.workspace.resolve()
        except (OSError, RuntimeError):
            # Resolution failed (broken symlink chain, permission, etc.) —
            # treat as outside so we err on the side of asking.
            return False
        try:
            resolved.relative_to(workspace)
            return True
        except ValueError:
            return False


# ---------- match helpers ----------

def _user_path() -> Path:
    """Look up USER_PERMISSIONS_PATH at call time so the package __init__
    can monkey-patch it (tests do this)."""
    from . import io as _io
    from .. import permissions as _pkg
    # Prefer the package-level binding (tests patch it there), fall back to io.
    return getattr(_pkg, "USER_PERMISSIONS_PATH", _io.USER_PERMISSIONS_PATH)


def _project_path() -> Path:
    from . import io as _io
    from .. import permissions as _pkg
    return getattr(_pkg, "PROJECT_PERMISSIONS_PATH", _io.PROJECT_PERMISSIONS_PATH)


def _expand_for_path_tool(s: str) -> str:
    """Expand a leading `~` so two equivalent path strings compare equal."""
    if s.startswith("~"):
        return str(Path(s).expanduser())
    return s


def _match(rule: Rule, tool: str, arg_str: str) -> bool:
    if rule.tool not in (tool, "*"):
        return False
    pattern = rule.pattern
    if tool in PATH_TOOLS:
        arg_str = _expand_for_path_tool(arg_str)
        pattern = _expand_for_path_tool(pattern)
    return fnmatch.fnmatchcase(arg_str, pattern)


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
