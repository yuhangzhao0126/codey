"""Built-in hooks shipped with codey.

`build_default_hooks(...)` returns a fully-populated HookRegistry. The host
(CLI/TUI) supplies UI-specific sinks (transcript writer, meta-line writer)
plus the permission engine + approve callback. Optionally, a `todo_tool`
+ `todo_writer` enable the todo-list nag + render hooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..registry import HookEvent, HookRegistry
from ...permissions import PermissionEngine
from ...tools.todo_write import TodoWriteTool
from .audit_log import audit_log_hook
from .otel import build_otel_hooks, otel_enabled
from .permission import ApproveFn, permission_check_hook
from .skill_render import build_skill_render_hook
from .stop_logger import stop_logger_hook
from .subagent_render import build_subagent_render_hooks
from .todo_nag import build_todo_nag_hooks
from .todo_render import TodoWriter, todo_render_hook
from .transcript import post_tool_render_hook, pre_tool_render_hook

# UI sinks injected by the host. transcript_writer carries (style, text) so
# the host can color tool-call/ok/err lines differently. meta_writer is a
# plain dim/info line writer used by stop_logger and hook errors.
TranscriptWriter = Callable[[str, str], None]   # (style, text)
MetaWriter = Callable[[str], None]              # (text)


def build_default_hooks(
    *,
    engine: PermissionEngine,
    approve: ApproveFn | None,
    transcript_writer: TranscriptWriter | None,
    meta_writer: MetaWriter,
    audit_log_path: Path | None = None,
    todo_tool: TodoWriteTool | None = None,
    todo_writer: TodoWriter | None = None,
    session_id: str | None = None,
    otel: dict | None = None,
) -> HookRegistry:
    reg = HookRegistry(error_sink=meta_writer)

    # Permission MUST come first in PreToolUse so a Deny / user-deny short-
    # circuits before audit / transcript record an event that never ran.
    reg.register(HookEvent.PRE_TOOL_USE,
                 permission_check_hook(engine=engine, approve=approve),
                 name="permission")

    # Per-call → / ← transcript lines are opt-in. Hosts that want a quiet
    # transcript (the TUI, post-PR-C) pass transcript_writer=None and rely
    # on the audit log instead.
    if transcript_writer is not None:
        reg.register(HookEvent.PRE_TOOL_USE,
                     pre_tool_render_hook(transcript_writer),
                     name="transcript_pre")
        reg.register(HookEvent.POST_TOOL_USE,
                     post_tool_render_hook(transcript_writer),
                     name="transcript_post")

    reg.register(HookEvent.PRE_TOOL_USE,
                 audit_log_hook("PreToolUse", log_path=audit_log_path,
                                session_id=session_id),
                 name="audit_log_pre")
    reg.register(HookEvent.POST_TOOL_USE,
                 audit_log_hook("PostToolUse", log_path=audit_log_path,
                                session_id=session_id),
                 name="audit_log_post")

    reg.register(HookEvent.STOP,
                 stop_logger_hook(meta_writer),
                 name="stop_logger")

    # Sub-agent meta lines on the parent transcript: ⏵ on spawn_agent
    # PreToolUse, ⏷ on PostToolUse. Skipped on children (see build_child_hooks).
    sub_pre, sub_post = build_subagent_render_hooks(meta_writer)
    reg.register(HookEvent.PRE_TOOL_USE,  sub_pre,  name="subagent_render_pre")
    reg.register(HookEvent.POST_TOOL_USE, sub_post, name="subagent_render_post")

    # Skill-load meta line on the parent transcript: ↳ on PostToolUse for
    # load_skill. Skipped on children (their meta_writer is a no-op anyway).
    reg.register(HookEvent.POST_TOOL_USE,
                 build_skill_render_hook(meta_writer),
                 name="skill_render")

    # Todo hooks are only registered if the host supplied the tool + writer.
    # Headless / test callers can skip them.
    if todo_tool is not None and todo_writer is not None:
        pre, post, stop = build_todo_nag_hooks()
        reg.register(HookEvent.PRE_TOOL_USE,  pre,  name="todo_nag_pre")
        # Register todo_render BEFORE todo_nag_post so the rendered list
        # appears in the UI before the nag reminder (if any). Both run.
        reg.register(HookEvent.POST_TOOL_USE,
                     todo_render_hook(tool=todo_tool, writer=todo_writer),
                     name="todo_render")
        reg.register(HookEvent.POST_TOOL_USE, post, name="todo_nag_post")
        reg.register(HookEvent.STOP,          stop, name="todo_nag_stop")

    # OTel tracing — opt-in via the otel dict (built by the host from the
    # --otel flag / CODEY_OTEL env var / [otel] config block).
    if otel is not None:
        cbs = build_otel_hooks(
            session_id=session_id or "",
            profile_name=otel["profile_name"],
            model=otel["model"],
            base_url=otel["base_url"],
            service_name=otel.get("service_name"),
            endpoint=otel.get("endpoint"),
            tracer_provider=otel.get("tracer_provider"),
        )
        reg.register(HookEvent.USER_PROMPT_SUBMIT, cbs["user_prompt_submit"], name="otel_turn_start")
        reg.register(HookEvent.PRE_TOOL_USE,       cbs["pre_tool_use"],       name="otel_tool_pre")
        reg.register(HookEvent.POST_TOOL_USE,      cbs["post_tool_use"],      name="otel_tool_post")
        reg.register(HookEvent.STOP,               cbs["stop"],               name="otel_turn_stop")

    return reg


def build_child_hooks(
    *,
    engine: PermissionEngine,
    approve: ApproveFn | None,
    audit_log_path: Path | None = None,
    meta_writer: MetaWriter,
    session_id: str,
    parent_session_id: str,
    description: str | None = None,
) -> HookRegistry:
    """Curated hook registry for a sub-agent Agent.

    Shares the permission engine + approve callback + audit log + meta writer
    with the parent, but registers only the hooks that make sense for a
    child:

      PRE_TOOL_USE   permission  → audit_log_pre
      POST_TOOL_USE  audit_log_post
      STOP           (intentionally none — see below)

    Deliberately omitted:
      - stop_logger: would print "[turn finished]" mid-parent-turn for every
        child completion. Children stop silently from the user's POV; the
        spawn_agent meta line covers the visible signal.
      - transcript_pre/post: null in TUI anyway; child tool calls live in
        the audit log and the /subs panel.
      - todo_nag / todo_render: children don't have todo_write in their tool
        registry, so these would either no-op or double-render the parent's
        todos.
      - subagent_render: children can't spawn further children, so this would
        never fire — leave it off to keep the child registry minimal.
      - skill_render: children share the parent's skill registry and could
        call load_skill, but the meta line would be invisible (child's
        meta_writer is the same parent writer but the line lands mid-parent-
        turn with no context); the audit log captures every child load_skill
        call regardless. Cleaner to leave it off, same reasoning as
        stop_logger.
      - OTel: child-span support is a v2 follow-up; the current OTel hook
        is shaped around per-turn spans for a single agent.

    Every audit-log line emitted by a child carries `parent_session_id` so
    `jq` can reconstruct the parent→child causal chain.

    `description` (optional) becomes the requester label on every approval
    modal this child triggers, e.g. 'sub-agent[2] "investigate-db"'. The
    sub-index is parsed from `session_id` (format: '<parent>.sub.<N>').
    """
    requester: str | None = None
    if description is not None:
        # session_id is "<parent>.sub.<N>"; extract N for the user-visible label.
        suffix = session_id.rsplit(".sub.", 1)
        idx = suffix[1] if len(suffix) == 2 else "?"
        requester = f'sub-agent[{idx}] "{description}"'

    reg = HookRegistry(error_sink=meta_writer)
    reg.register(HookEvent.PRE_TOOL_USE,
                 permission_check_hook(engine=engine, approve=approve,
                                       requester=requester),
                 name="permission")
    reg.register(HookEvent.PRE_TOOL_USE,
                 audit_log_hook("PreToolUse", log_path=audit_log_path,
                                session_id=session_id,
                                parent_session_id=parent_session_id),
                 name="audit_log_pre")
    reg.register(HookEvent.POST_TOOL_USE,
                 audit_log_hook("PostToolUse", log_path=audit_log_path,
                                session_id=session_id,
                                parent_session_id=parent_session_id),
                 name="audit_log_post")
    return reg


__all__ = [
    "build_default_hooks",
    "build_child_hooks",
    "audit_log_hook",
    "build_otel_hooks",
    "otel_enabled",
    "permission_check_hook",
    "pre_tool_render_hook",
    "post_tool_render_hook",
    "stop_logger_hook",
    "build_todo_nag_hooks",
    "todo_render_hook",
    "TodoWriter",
]
