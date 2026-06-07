"""SpawnAgentTool: spawn an isolated sub-agent.

Pure tool: no permission logic (that lives in the PreToolUse permission
hook on the PARENT's loop — the hook decides whether spawn_agent itself
is allowed). No UI rendering (the subagent_render hook handles meta
lines). Returns a string, never raises — every failure becomes
"error: ..." so the model can react.

The tool needs the live Session to construct children (profile resolution,
shared engine, shared audit writer). Because tools are normally
constructor-arg-free and registered in build_default_registry, we accept
a session_provider callable and register the tool from Session.build
AFTER the Session is fully constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..core.events import AssistantMessageCompleted, TurnCompleted

if TYPE_CHECKING:
    from ..core.session import Session

MAX_OUTPUT_CHARS = 10_000


@dataclass
class SpawnAgentTool:
    session_provider: Callable[[], "Session"] = None

    name: str = "spawn_agent"
    description: str = (
        "Spawn an isolated sub-agent to perform a task with a fresh context "
        "window. Use for independent investigations or work that would "
        "otherwise bloat your context. Multiple `spawn_agent` calls in one "
        "turn run concurrently. The sub-agent has the same tools and "
        "permissions as you, except it cannot spawn further sub-agents and "
        "cannot edit the todo list. Returns only the sub-agent's final "
        "message — its full transcript is not preserved (use the /subs "
        "slash command in the UI to inspect a sub-agent's full run)."
    )
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.parameters = {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": (
                        "Short 3-5 word label for this sub-agent run, shown "
                        "in the UI meta line and the /subs panel."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "The full task for the sub-agent. Must be "
                        "self-contained — the sub-agent inherits no context "
                        "from this conversation. Include all relevant file "
                        "paths, goals, constraints. The sub-agent's final "
                        "message is the only thing returned to you, so "
                        "instruct it to end with a complete summary."
                    ),
                },
                "profile": {
                    "type": "string",
                    "description": (
                        "Optional profile name from ~/.config/codey/config.toml. "
                        "Defaults to the parent's profile."
                    ),
                },
            },
            "required": ["description", "prompt"],
            "additionalProperties": False,
        }

    async def run(self, arguments: dict[str, Any]) -> str:
        description = (arguments.get("description") or "").strip() or "(no description)"
        prompt = arguments.get("prompt") or ""
        profile_name = arguments.get("profile") or None

        if not prompt:
            return "error: spawn_agent requires a non-empty `prompt`"

        sess = self.session_provider() if self.session_provider else None
        if sess is None:
            return "error: spawn_agent is not wired to a session"

        # Build the child. cfg.resolve raises RuntimeError on unknown profile
        # — translate to the tool's string-error contract.
        try:
            child, child_id = sess.build_child_agent(
                description=description, profile_name=profile_name,
            )
        except RuntimeError as e:
            return f"error: unknown profile: {e}"
        except Exception as e:  # noqa: BLE001
            return f"error: failed to construct sub-agent: {type(e).__name__}: {e}"

        # Record the description so /subs can show it later.
        sess.subagent_recorder.set_label(child_id, description)

        last_text = ""
        terminal_reason = "stop"
        terminal_error: str | None = None
        try:
            try:
                async for ev in child.run(prompt):
                    sess.subagent_recorder.append(child_id, ev)
                    if isinstance(ev, AssistantMessageCompleted):
                        last_text = ev.text
                    elif isinstance(ev, TurnCompleted):
                        terminal_reason = ev.reason
                        terminal_error = ev.error
            except (KeyboardInterrupt, GeneratorExit):
                raise
            except BaseException as e:  # noqa: BLE001
                terminal_reason = "error"
                terminal_error = f"{type(e).__name__}: {e}"
        finally:
            try:
                await child.aclose()
            except Exception:  # noqa: BLE001
                pass

        if terminal_reason == "cancelled":
            return "error: cancelled"
        if terminal_reason == "error":
            return f"error: sub-agent failed: {terminal_error or 'unknown error'}"
        if not last_text:
            return "error: sub-agent ended without a final message"
        if len(last_text) > MAX_OUTPUT_CHARS:
            head = last_text[: MAX_OUTPUT_CHARS // 2]
            tail = last_text[-MAX_OUTPUT_CHARS // 2:]
            return f"{head}\n…[truncated {len(last_text) - MAX_OUTPUT_CHARS} chars]…\n{tail}"
        return last_text
