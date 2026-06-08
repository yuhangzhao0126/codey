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
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from ..config import ConfigFile, Profile
from ..hooks import HookRegistry
from ..hooks.builtin import build_default_hooks
from ..permissions import PermissionEngine
from ..prompt import build_system_prompt
from ..skills import SkillRegistry
from ..tools import build_default_registry
from .agent import ToolRegistry
from .turn import Agent


_PACKAGE_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills_bundled"
_USER_SKILLS_DIR    = Path.home() / ".config" / "codey" / "skills"


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
class SubAgentRecorder:
    """In-memory store of every event each sub-agent emitted.

    Bounded per child to keep memory predictable; the audit log is the
    persistent record. Read by the /subs panel — snapshot-on-open, no
    live tail.
    """
    per_child_cap: int = 10_000
    _events: dict[str, deque] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    _labels: dict[str, str] = field(default_factory=dict)

    def append(self, child_session_id: str, event: Any) -> None:
        bucket = self._events.get(child_session_id)
        if bucket is None:
            bucket = deque(maxlen=self.per_child_cap)
            self._events[child_session_id] = bucket
            self._order.append(child_session_id)
        bucket.append(event)

    def events_for(self, child_session_id: str) -> list:
        return list(self._events.get(child_session_id, ()))

    def children(self) -> list[str]:
        return list(self._order)

    def set_label(self, child_session_id: str, description: str) -> None:
        self._labels[child_session_id] = description

    def label_for(self, child_session_id: str) -> str:
        return self._labels.get(child_session_id, "(no description)")


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
    skills: SkillRegistry = field(default_factory=SkillRegistry)
    subagent_recorder: SubAgentRecorder = field(default_factory=SubAgentRecorder)
    _sub_counter: int = 0
    _ui_approve: Any = None
    _audit_log_path: Any = None
    _meta_writer: Any = None

    @classmethod
    def build(
        cls,
        *,
        profile_arg: str | None,
        ui_sinks: UISinks,
        workspace: Path | None = None,
        otel_enabled: bool = False,
    ) -> "Session":
        cfg = ConfigFile.load()
        profile = cfg.resolve(profile_arg)
        ws = (workspace or Path.cwd()).resolve()
        engine = PermissionEngine.load(workspace=ws)
        tools = build_default_registry()
        skills = SkillRegistry.scan(
            package_root=_PACKAGE_SKILLS_DIR,
            user_root=_USER_SKILLS_DIR,
            project_root=ws / ".codey" / "skills",
        )
        session_id = uuid.uuid4().hex[:8]
        otel_cfg: dict | None = None
        if otel_enabled:
            otel_cfg = {
                "profile_name": profile.name,
                "model": profile.model,
                "base_url": profile.base_url,
            }
        recent_reads: deque = deque(maxlen=5)
        hooks = build_default_hooks(
            engine=engine,
            approve=ui_sinks.approve,
            transcript_writer=ui_sinks.transcript_writer,
            meta_writer=ui_sinks.meta_writer,
            todo_tool=tools.tools.get("todo_write"),
            todo_writer=ui_sinks.todo_writer,
            session_id=session_id,
            otel=otel_cfg,
            recent_reads_deque=recent_reads,
        )
        agent = Agent(
            profile=profile,
            system_prompt=build_system_prompt(skills=skills),
            tools=tools,
            hooks=hooks,
            session_id=session_id,
            _meta=ui_sinks.meta_writer,
            _recent_reads=recent_reads,
        )
        sess = cls(profile=profile, workspace=ws, cfg=cfg, agent=agent,
                   engine=engine, hooks=hooks, tools=tools,
                   session_id=session_id, skills=skills)
        # Capture for build_child_agent (need these to build child hooks).
        sess._ui_approve = ui_sinks.approve
        sess._meta_writer = ui_sinks.meta_writer

        from ..tools.load_skill import LoadSkillTool
        tools.register(LoadSkillTool(skills=skills))

        from ..tools.spawn_agent import SpawnAgentTool
        tools.register(SpawnAgentTool(session_provider=lambda: sess))

        from ..tools.compact import CompactTool
        tools.register(CompactTool(session_provider=lambda: sess))
        return sess

    async def swap_profile(self, name: str) -> Profile:
        new_profile = self.cfg.resolve(name)
        await self.agent.swap_profile(new_profile)
        self.profile = new_profile
        return new_profile

    def build_child_agent(
        self,
        *,
        description: str,
        profile_name: str | None = None,
    ) -> tuple[Agent, str]:
        """Construct a sub-agent. The ONLY place that knows how.

        Returns (child_agent, child_session_id). The caller (SpawnAgentTool)
        is responsible for draining the child's run() and calling
        child.aclose() when done.
        """
        from ..hooks.builtin import build_child_hooks
        from ..prompt import build_subagent_system_prompt

        child_profile = self.profile if profile_name is None else self.cfg.resolve(profile_name)

        self._sub_counter += 1
        child_id = f"{self.session_id}.sub.{self._sub_counter}"

        EXCLUDED_FROM_CHILD = {"spawn_agent", "todo_write", "compact"}
        child_tools = ToolRegistry(tools={
            n: t for n, t in self.tools.tools.items()
            if n not in EXCLUDED_FROM_CHILD
        })

        child_recent_reads: deque = deque(maxlen=5)
        child_hooks = build_child_hooks(
            engine=self.engine,
            approve=self._ui_approve,
            audit_log_path=self._audit_log_path,
            meta_writer=self._meta_writer,
            session_id=child_id,
            parent_session_id=self.session_id,
            description=description,
            recent_reads_deque=child_recent_reads,
        )

        child_system = build_subagent_system_prompt(
            description, cwd=self.workspace, skills=self.skills,
        )

        child = Agent(
            profile=child_profile,
            system_prompt=child_system,
            tools=child_tools,
            hooks=child_hooks,
            session_id=child_id,
            _meta=self._meta_writer,
            _recent_reads=child_recent_reads,
        )
        return child, child_id

    async def aclose(self) -> None:
        await self.agent.aclose()
