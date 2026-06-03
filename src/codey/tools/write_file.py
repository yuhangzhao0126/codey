"""write_file: write text to a file (creating parents). Requires approval."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

ApproveFn = Callable[[str], "bool | Awaitable[bool]"]

MAX_CONTENT_BYTES = 1_000_000


@dataclass
class WriteFileTool:
    approve: ApproveFn | None = None

    name: str = "write_file"
    description: str = (
        "Write UTF-8 text content to a file, OVERWRITING it if it exists. "
        "Parent directories are created as needed. Requires user approval "
        "before each write — the user may deny. Use this to create new files "
        "or rewrite small files from scratch; for edits to existing files prefer "
        "apply_edit so you don't accidentally clobber unrelated content."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the file to write (absolute or cwd-relative).",
                },
                "content": {
                    "type": "string",
                    "description": "Full file contents to write.",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        path_str = (arguments.get("path") or "").strip()
        content = arguments.get("content")
        if not path_str:
            return "error: empty path"
        if content is None:
            return "error: missing content"
        if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
            return f"error: content exceeds {MAX_CONTENT_BYTES} bytes"

        path = Path(path_str).expanduser()
        action = f"write {len(content)} chars to {path}"
        if not await self._ask(action):
            return f"error: user denied permission to {action}"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except IsADirectoryError:
            return f"error: path is a directory: {path}"
        except PermissionError:
            return f"error: permission denied: {path}"
        except OSError as e:
            return f"error: {type(e).__name__}: {e}"

        return f"ok: wrote {len(content)} chars to {path}"

    async def _ask(self, command: str) -> bool:
        if self.approve is None:
            return True
        result = self.approve(command)
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)
