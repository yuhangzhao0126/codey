"""Permissions package — re-exports the same public API the old
`codey.permissions` module exposed, so every existing import keeps working.
"""

from __future__ import annotations

from .engine import PermissionEngine, _expand_for_path_tool, _first_match, _match
from .io import (
    PROJECT_PERMISSIONS_PATH,
    USER_PERMISSIONS_PATH,
    _load_file,
    _toml_escape,
    _write_file,
    load_file,
    write_file,
)
from .rules import (
    BUILTIN_ALLOW,
    BUILTIN_DENY,
    MODE_DESCRIPTIONS,
    PATH_TOOLS,
    READER_TOOLS,
    WRITER_TOOLS,
    Action,
    Allow,
    Ask,
    Decision,
    Deny,
    Mode,
    Rule,
    Verdict,
)
from .suggest import suggest_pattern

__all__ = [
    "Action",
    "Allow",
    "Ask",
    "BUILTIN_ALLOW",
    "BUILTIN_DENY",
    "Decision",
    "Deny",
    "MODE_DESCRIPTIONS",
    "Mode",
    "PATH_TOOLS",
    "PROJECT_PERMISSIONS_PATH",
    "PermissionEngine",
    "READER_TOOLS",
    "Rule",
    "USER_PERMISSIONS_PATH",
    "Verdict",
    "WRITER_TOOLS",
    "load_file",
    "suggest_pattern",
    "write_file",
]
