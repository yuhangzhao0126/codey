"""read_file: read a UTF-8 text file from disk and return its contents.

Permission gating is handled by the PreToolUse permission hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_BYTES = 200_000


@dataclass
class ReadFileTool:
    name: str = "read_file"
    description: str = (
        "Read the contents of a UTF-8 text file from the local filesystem. "
        "Returns the full file contents (capped at 200KB). Use this to inspect "
        "source files, configs, logs, etc. before deciding what to change. "
        "Returns an error string (starting with 'error:') if the file is missing, "
        "binary, or too large. Subject to the active permission rules — paths "
        "outside the workspace require user approval."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or cwd-relative path to the file.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        path_str = (arguments.get("path") or "").strip()
        if not path_str:
            return "error: empty path"

        path = Path(path_str).expanduser()
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return f"error: file not found: {path}"
        except IsADirectoryError:
            return f"error: path is a directory: {path}"
        except PermissionError:
            return f"error: permission denied: {path}"
        except OSError as e:
            return f"error: {type(e).__name__}: {e}"

        if len(data) > MAX_BYTES:
            return f"error: file is {len(data)} bytes (max {MAX_BYTES})"

        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return "error: file is not valid UTF-8 (likely binary)"
