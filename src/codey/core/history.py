"""History management for the agent's chat-log.

`repair()` drops trailing assistant.tool_calls messages whose tool results
never landed (e.g. user cancelled mid-tool-call last turn, or a tool
dispatch raised). The OpenAI API 400s if it sees an assistant message with
tool_calls that isn't followed by one role:"tool" message per call_id, so
we run repair at the top of every turn.

Pure function over a list of Messages — no Agent dependency, fully testable
in isolation.
"""

from __future__ import annotations

from .messages import Message


def repair(history: list[Message]) -> None:
    """Drop trailing orphan assistant.tool_calls / role:tool messages in place.

    Walks from the end backwards: any assistant message with tool_calls must be
    followed by one role:"tool" message per call_id. Mutates the list in place.
    """
    while history:
        last = history[-1]
        if last.role == "assistant" and last.tool_calls:
            history.pop()
            continue
        if last.role == "tool":
            # tool result with no preceding assistant tool_calls is also junk.
            # Check by walking back to find the matching assistant call.
            ids_seen: set[str] = set()
            i = len(history) - 1
            while i >= 0 and history[i].role == "tool":
                if history[i].tool_call_id:
                    ids_seen.add(history[i].tool_call_id)
                i -= 1
            if i < 0 or history[i].role != "assistant" or not history[i].tool_calls:
                # orphaned tool block; drop just the trailing tool messages
                del history[i + 1:]
                continue
            expected = {c["id"] for c in history[i].tool_calls if c.get("id")}
            if expected - ids_seen:
                # Some tool results are missing for this assistant call;
                # drop the whole assistant + tool block.
                del history[i:]
                continue
        break


def reset_non_system(history: list[Message]) -> list[Message]:
    """Return a new history list containing only the system messages."""
    return [m for m in history if m.role == "system"]
