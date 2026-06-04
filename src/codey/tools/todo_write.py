"""todo_write: maintain a per-session task list to plan multi-step work.

Stateful tool: `self.todos` holds the current list. Each call replaces the
entire list (full-replace API — simpler than ops, matches Claude Code's
TodoWrite). Permission gating: trivially allowed (in-memory only). The
TUI/CLI rendering and the nag-reminder logic live in separate hooks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["pending", "in_progress", "completed"]
_VALID_STATUSES = ("pending", "in_progress", "completed")
MAX_ITEMS = 50
MAX_CONTENT_LEN = 500


@dataclass
class Todo:
    id: int
    content: str
    status: Status


@dataclass
class TodoWriteTool:
    name: str = "todo_write"
    description: str = (
        "Create or replace the session task list. Use this to plan multi-step "
        "work: lay out the steps up front with status='pending', mark one "
        "'in_progress' as you start it, and 'completed' when done. Always send "
        "the FULL updated list (the call replaces the previous list entirely). "
        "At most one item may be in_progress at a time. Returns a short "
        "confirmation; the UI renders the list to the user."
    )
    parameters: dict[str, Any] = None  # type: ignore[assignment]
    todos: list[Todo] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "The full task list (replaces previous).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":      {"type": "integer",
                                        "description": "Optional; assigned if omitted."},
                            "content": {"type": "string",
                                        "description": "Short imperative task description."},
                            "status":  {"type": "string",
                                        "enum": list(_VALID_STATUSES)},
                        },
                        "required": ["content", "status"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["todos"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        raw = arguments.get("todos")
        if not isinstance(raw, list):
            return "error: 'todos' must be a list"
        if len(raw) > MAX_ITEMS:
            return f"error: too many items ({len(raw)}; max {MAX_ITEMS})"

        new_list: list[Todo] = []
        in_progress_count = 0
        for i, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                return f"error: item {i} is not an object"
            content = item.get("content")
            status = item.get("status")
            if not isinstance(content, str) or not content.strip():
                return f"error: item {i} has empty content"
            if len(content) > MAX_CONTENT_LEN:
                return f"error: item {i} content too long (max {MAX_CONTENT_LEN} chars)"
            if status not in _VALID_STATUSES:
                return (f"error: item {i} has invalid status {status!r} "
                        f"(want one of {list(_VALID_STATUSES)})")
            if status == "in_progress":
                in_progress_count += 1
                if in_progress_count > 1:
                    return "error: only one item may be in_progress at a time"
            new_list.append(Todo(id=i, content=content.strip(), status=status))

        self.todos = new_list
        return f"todo list updated: {len(new_list)} item(s)"
