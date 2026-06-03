"""Built-in hooks shipped with codey.

`build_default_hooks(...)` returns a fully-populated HookRegistry. The host
(CLI/TUI) supplies UI-specific sinks (transcript writer, meta-line writer)
plus the permission engine + approve callback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..hooks import HookEvent, HookRegistry
from ..permissions import PermissionEngine
from .audit_log import audit_log_hook
from .permission import ApproveFn, permission_check_hook
from .stop_logger import stop_logger_hook
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
    transcript_writer: TranscriptWriter,
    meta_writer: MetaWriter,
    audit_log_path: Path | None = None,
) -> HookRegistry:
    reg = HookRegistry(error_sink=meta_writer)

    # Permission MUST come first in PreToolUse so a Deny / user-deny short-
    # circuits before audit / transcript record an event that never ran.
    reg.register(HookEvent.PRE_TOOL_USE,
                 permission_check_hook(engine=engine, approve=approve),
                 name="permission")

    reg.register(HookEvent.PRE_TOOL_USE,
                 pre_tool_render_hook(transcript_writer),
                 name="transcript_pre")
    reg.register(HookEvent.PRE_TOOL_USE,
                 audit_log_hook("PreToolUse", log_path=audit_log_path),
                 name="audit_log_pre")

    reg.register(HookEvent.POST_TOOL_USE,
                 post_tool_render_hook(transcript_writer),
                 name="transcript_post")
    reg.register(HookEvent.POST_TOOL_USE,
                 audit_log_hook("PostToolUse", log_path=audit_log_path),
                 name="audit_log_post")

    reg.register(HookEvent.STOP,
                 stop_logger_hook(meta_writer),
                 name="stop_logger")

    return reg


__all__ = [
    "build_default_hooks",
    "audit_log_hook",
    "permission_check_hook",
    "pre_tool_render_hook",
    "post_tool_render_hook",
    "stop_logger_hook",
]
