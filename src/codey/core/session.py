"""Session — one handle that bundles everything the UI needs.

Before this lived as 6 lines of wiring inside CodeyApp.on_mount and an
identical 6 lines inside cli._run(). Now there's one Session.build() entry
point. Adding cross-cutting state (token budget, session id, transcript
export) has one obvious home.

The host (TUI) supplies a UISinks bundle holding the writers/approver the
hooks need. Session.build() composes Profile + PermissionEngine +
ToolRegistry + HookRegistry + Agent and returns the bundle.

Behavior is identical to the pre-refactor wiring — this is pure relocation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from ..config import ConfigFile, Profile
from ..hooks import HookRegistry
from ..hooks.builtin import build_default_hooks
from ..permissions import PermissionEngine
from ..prompt import build_system_prompt
from ..tools import build_default_registry
from .agent import ToolRegistry
from .turn import Agent


class UISinks(Protocol):
    """Structural type for the UI-supplied writers + approver.

    Defined as a Protocol so Session.build() doesn't import from ui/. The
    concrete bundle lives in codey.ui.renderers.UISinks.

    `transcript_writer` is optional: None disables the per-call → / ←
    transcript hook (quiet TUI mode); the audit log still records every
    call regardless.
    """
    transcript_writer: Callable[[str, str], None] | None
    meta_writer:       Callable[[str], None]
    approve:           Callable[[dict], Awaitable[Any]]
    todo_writer:       Callable[[list], None] | None


@dataclass
class Session:
    profile: Profile
    workspace: Path
    cfg: ConfigFile
    agent: Agent
    engine: PermissionEngine
    hooks: HookRegistry
    tools: ToolRegistry
    session_id: str

    @classmethod
    def build(
        cls,
        *,
        profile_arg: str | None,
        ui_sinks: UISinks,
        workspace: Path | None = None,
    ) -> "Session":
        cfg = ConfigFile.load()
        profile = cfg.resolve(profile_arg)
        ws = (workspace or Path.cwd()).resolve()
        engine = PermissionEngine.load(workspace=ws)
        tools = build_default_registry()
        session_id = uuid.uuid4().hex[:8]
        hooks = build_default_hooks(
            engine=engine,
            approve=ui_sinks.approve,
            transcript_writer=ui_sinks.transcript_writer,
            meta_writer=ui_sinks.meta_writer,
            todo_tool=tools.tools.get("todo_write"),
            todo_writer=ui_sinks.todo_writer,
            session_id=session_id,
        )
        agent = Agent(
            profile=profile,
            system_prompt=build_system_prompt(),
            tools=tools,
            hooks=hooks,
        )
        return cls(profile=profile, workspace=ws, cfg=cfg, agent=agent,
                   engine=engine, hooks=hooks, tools=tools,
                   session_id=session_id)

    async def swap_profile(self, name: str) -> Profile:
        new_profile = self.cfg.resolve(name)
        await self.agent.swap_profile(new_profile)
        self.profile = new_profile
        return new_profile

    async def aclose(self) -> None:
        await self.agent.aclose()
