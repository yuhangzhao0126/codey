"""list_dir: list directory entries as structured `name | type | size` rows.

Permission gating is handled by the PreToolUse permission hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_ENTRIES = 500


@dataclass
class ListDirTool:
    name: str = "list_dir"
    description: str = (
        "List the entries of a directory. Returns one row per entry in the form "
        "`type  size  name` where type is `dir`, `file`, or `link`, size is bytes "
        "(or `-` for non-files), and entries are sorted alphabetically. By default "
        "hidden entries (starting with `.`) are skipped. Capped at 500 entries. "
        "Subject to the active permission rules — paths outside the workspace "
        "require user approval."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path (default: current working directory).",
                    "default": ".",
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include entries starting with `.` (default false).",
                    "default": False,
                },
            },
            "required": [],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        path_str = (arguments.get("path") or ".").strip() or "."
        show_hidden = bool(arguments.get("show_hidden", False))

        path = Path(path_str).expanduser()
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name)
        except FileNotFoundError:
            return f"error: directory not found: {path}"
        except NotADirectoryError:
            return f"error: not a directory: {path}"
        except PermissionError:
            return f"error: permission denied: {path}"
        except OSError as e:
            return f"error: {type(e).__name__}: {e}"

        rows: list[str] = []
        truncated = False
        for entry in entries:
            if not show_hidden and entry.name.startswith("."):
                continue
            if len(rows) >= MAX_ENTRIES:
                truncated = True
                break
            try:
                if entry.is_symlink():
                    kind = "link"
                    size_str = "-"
                elif entry.is_dir():
                    kind = "dir"
                    size_str = "-"
                else:
                    kind = "file"
                    size_str = str(entry.stat().st_size)
            except OSError:
                kind = "?"
                size_str = "-"
            rows.append(f"{kind:<4}  {size_str:>10}  {entry.name}")

        if not rows:
            return f"(empty: {path})"

        out = "\n".join(rows)
        if truncated:
            out += f"\n…[truncated at {MAX_ENTRIES} entries]"
        return out
