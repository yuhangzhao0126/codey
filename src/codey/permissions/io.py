"""TOML I/O for the permissions config files.

Loads `~/.config/codey/permissions.toml` (user mode + user rules) and
`./.codey/permissions.toml` (project rules). Writes go through here too.

File format:
    mode = "safe"          # optional, user file only

    [[rules]]
    tool    = "bash"
    pattern = "git push*"
    action  = "allow"      # allow | deny | ask
    reason  = "user-added"
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .rules import Mode, Rule

USER_PERMISSIONS_PATH = Path.home() / ".config" / "codey" / "permissions.toml"
PROJECT_PERMISSIONS_PATH = Path(".codey") / "permissions.toml"


def load_file(path: Path, source: str) -> tuple[Mode | None, list[Rule]]:
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


def write_file(path: Path, *, mode: Mode | None, rules: list[Rule]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if mode is not None:
        lines.append(f'mode = "{mode.value}"\n')
    for r in rules:
        lines.append("\n[[rules]]\n")
        lines.append(f'tool    = "{toml_escape(r.tool)}"\n')
        lines.append(f'pattern = "{toml_escape(r.pattern)}"\n')
        lines.append(f'action  = "{r.action}"\n')
        if r.reason:
            lines.append(f'reason  = "{toml_escape(r.reason)}"\n')
    path.write_text("".join(lines), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# Back-compat aliases for callers that imported the old underscored names.
_load_file = load_file
_write_file = write_file
_toml_escape = toml_escape
