"""write_file: write text to a file (creating parents). Permission-gated."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..permissions import Allow, Ask, Deny, PermissionEngine, Rule, suggest_pattern
from .bash import ApproveFn, Verdict

MAX_CONTENT_BYTES = 1_000_000


@dataclass
class WriteFileTool:
    engine: PermissionEngine = None  # type: ignore[assignment]
    approve: ApproveFn | None = None

    name: str = "write_file"
    description: str = (
        "Write UTF-8 text content to a file, OVERWRITING it if it exists. "
        "Parent directories are created as needed. Subject to the active "
        "permission rules; typically requires user approval. Use this to "
        "create new files or rewrite small files from scratch; for edits to "
        "existing files prefer apply_edit so you don't clobber unrelated content."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.engine is None:
            self.engine = PermissionEngine()
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

        decision = self.engine.check("write_file", path_str)
        if isinstance(decision, Deny):
            return f"error: blocked by permission rule: {decision.reason}"
        if isinstance(decision, Ask):
            verdict = await self._ask({
                "tool": "write_file",
                "command": f"write {len(content)} chars to {path_str}",
                "reason": decision.reason,
                "suggested_pattern": suggest_pattern("write_file", path_str),
            })
            if not verdict.allowed:
                return f"error: user denied permission to write to {path_str}"
            if verdict.remember and verdict.remember_pattern:
                rule = Rule(
                    tool="write_file",
                    pattern=verdict.remember_pattern,
                    action=verdict.remember_action,  # type: ignore[arg-type]
                    reason="user-added via approval prompt",
                )
                if verdict.remember_scope == "user":
                    self.engine.append_user_rule(rule)
                else:
                    self.engine.append_project_rule(rule)

        path = Path(path_str).expanduser()
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

    async def _ask(self, ctx: dict[str, Any]) -> Verdict:
        if self.approve is None:
            return Verdict(allowed=True)
        result = self.approve(ctx)
        if asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, Verdict) else Verdict(allowed=bool(result))
