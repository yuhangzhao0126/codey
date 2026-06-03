"""grep: regex search across files.

Permission gating is handled by the PreToolUse permission hook.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_MATCHES = 200
MAX_FILE_BYTES = 1_000_000  # skip files larger than 1MB
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
})


@dataclass
class GrepTool:
    name: str = "grep"
    description: str = (
        "Search for a regex pattern across files under a directory. "
        "Returns matching lines in the form `path:lineno:matched-line`. "
        "Recursively walks the directory but skips common dependency / build "
        "folders (.git, .venv, node_modules, __pycache__, dist, build) and "
        "files larger than 1MB. Capped at 200 total matches. Subject to the "
        "active permission rules — paths outside the workspace require approval."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Python regular expression to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search under (default: cwd).",
                    "default": ".",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional glob to filter filenames (e.g. '*.py').",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive match (default false).",
                    "default": False,
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        pattern_str = arguments.get("pattern") or ""
        if not pattern_str:
            return "error: empty pattern"
        flags = re.IGNORECASE if arguments.get("case_insensitive") else 0
        try:
            regex = re.compile(pattern_str, flags)
        except re.error as e:
            return f"error: invalid regex: {e}"

        path_str = (arguments.get("path") or ".").strip() or "."
        root = Path(path_str).expanduser()
        glob = arguments.get("glob")

        if not root.exists():
            return f"error: path not found: {root}"

        if root.is_file():
            candidates = iter([root])
        else:
            candidates = self._walk(root, glob)

        matches: list[str] = []
        truncated = False
        for file_path in candidates:
            if len(matches) >= MAX_MATCHES:
                truncated = True
                break
            try:
                if file_path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{file_path}:{lineno}:{line}")
                    if len(matches) >= MAX_MATCHES:
                        truncated = True
                        break

        if not matches:
            return "(no matches)"
        out = "\n".join(matches)
        if truncated:
            out += f"\n…[truncated at {MAX_MATCHES} matches]"
        return out

    @staticmethod
    def _walk(root: Path, glob: str | None):
        """Yield files under root, skipping SKIP_DIRS and applying optional glob."""
        for sub in root.rglob("*"):
            if any(part in SKIP_DIRS for part in sub.parts):
                continue
            if not sub.is_file():
                continue
            if glob and not sub.match(glob):
                continue
            yield sub
