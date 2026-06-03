"""apply_edit: aider-style search/replace block editing.

Edit format (one or more blocks per request):

    <<<<<<< SEARCH
    old text exactly as it appears
    =======
    replacement text
    >>>>>>> REPLACE

Each block is applied in order. The SEARCH text must appear exactly ONCE in
the current file contents (after prior blocks in this request have been
applied) — otherwise the whole edit fails and the file is left untouched.

To delete code, leave the REPLACE side empty. To create a new file, set the
SEARCH block to an empty string and provide the new contents in REPLACE.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

ApproveFn = Callable[[str], "bool | Awaitable[bool]"]

_BLOCK_RE = re.compile(
    r"<{7} SEARCH\r?\n(.*?)\r?\n={7}\r?\n(.*?)\r?\n>{7} REPLACE",
    re.DOTALL,
)


@dataclass
class ApplyEditTool:
    approve: ApproveFn | None = None

    name: str = "apply_edit"
    description: str = (
        "Apply one or more search/replace edits to a file. Each edit is a block "
        "in this exact format:\n"
        "    <<<<<<< SEARCH\n"
        "    exact text to find\n"
        "    =======\n"
        "    replacement text\n"
        "    >>>>>>> REPLACE\n"
        "Concatenate multiple blocks back-to-back in the `edits` argument to "
        "make multiple edits in one call. The SEARCH side must match the file "
        "verbatim (including indentation) and appear EXACTLY ONCE; otherwise the "
        "whole operation aborts and the file is unchanged. Empty SEARCH creates "
        "a new file with the REPLACE content. Requires user approval."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit.",
                },
                "edits": {
                    "type": "string",
                    "description": "One or more SEARCH/REPLACE blocks, concatenated.",
                },
            },
            "required": ["path", "edits"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        path_str = (arguments.get("path") or "").strip()
        edits_text = arguments.get("edits") or ""
        if not path_str:
            return "error: empty path"
        if not edits_text:
            return "error: empty edits"

        blocks = _BLOCK_RE.findall(edits_text)
        if not blocks:
            return (
                "error: no SEARCH/REPLACE blocks found. Each edit must use the "
                "exact markers `<<<<<<< SEARCH`, `=======`, `>>>>>>> REPLACE`."
            )

        path = Path(path_str).expanduser()

        # Special case: a single block with empty SEARCH creates a new file.
        if len(blocks) == 1 and blocks[0][0] == "":
            new_content = blocks[0][1]
            if path.exists():
                return f"error: file exists; cannot create with empty SEARCH: {path}"
            action = f"create {path} ({len(new_content)} chars)"
            if not await self._ask(action):
                return f"error: user denied permission to {action}"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new_content, encoding="utf-8")
            except OSError as e:
                return f"error: {type(e).__name__}: {e}"
            return f"ok: created {path}"

        # Existing-file edits: load current contents.
        try:
            current = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return f"error: file not found: {path}"
        except UnicodeDecodeError:
            return "error: file is not valid UTF-8"
        except OSError as e:
            return f"error: {type(e).__name__}: {e}"

        # Apply blocks in order, in-memory. Fail-atomic: if any block doesn't
        # match exactly once, we abort without touching the file.
        working = current
        for i, (old, new) in enumerate(blocks, 1):
            if old == "":
                return f"error: block {i}: empty SEARCH only allowed for new files"
            count = working.count(old)
            if count == 0:
                return f"error: block {i}: SEARCH text not found in {path}"
            if count > 1:
                return (
                    f"error: block {i}: SEARCH text matches {count} places; "
                    "make it unique (add surrounding context)"
                )
            working = working.replace(old, new, 1)

        if working == current:
            return "error: edits produced no change"

        action = f"edit {path} ({len(blocks)} block{'s' if len(blocks) != 1 else ''})"
        if not await self._ask(action):
            return f"error: user denied permission to {action}"

        try:
            path.write_text(working, encoding="utf-8")
        except OSError as e:
            return f"error: {type(e).__name__}: {e}"

        return f"ok: applied {len(blocks)} edit(s) to {path}"

    async def _ask(self, command: str) -> bool:
        if self.approve is None:
            return True
        result = self.approve(command)
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)
