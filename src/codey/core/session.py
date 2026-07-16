"""Session — one handle that bundles everything the UI needs.

Before this lived as 6 lines of wiring inside CodeyApp.on_mount and an
identical 6 lines inside cli._run(). Now there's one Session.build() entry
point. Adding cross-cutting state (token budget, session id, transcript
export) has one obvious home.

The host (TUI) supplies a UISinks bundle holding the writers/approver the
hooks need. Session.build() composes Provider + PermissionEngine +
ToolRegistry + HookRegistry + Agent and returns the bundle.

Behavior is identical to the pre-refactor wiring — this is pure relocation.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from ..config import ConfigFile, Provider
from ..hooks import HookRegistry
from ..hooks.builtin import build_default_hooks
from ..permissions import PermissionEngine
from ..prompt import build_system_prompt
from ..session_store import SessionResumeError, SessionStore
from ..skills import SkillRegistry
from ..tools import build_default_registry
from .. import context as _context_pipeline
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
    provider: Provider
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
    _session_store: SessionStore | None = None
    memory_registry: Any = None
    memory_store: Any = None

    @classmethod
    def build(
        cls,
        *,
        provider_arg: str | None,
        ui_sinks: UISinks,
        workspace: Path | None = None,
        otel_enabled: bool = False,
    ) -> "Session":
        from datetime import datetime as _dt

        cfg = ConfigFile.load()
        provider = cfg.resolve(provider_arg)
        ws = (workspace or Path.cwd()).resolve()
        engine = PermissionEngine.load(workspace=ws)
        tools = build_default_registry()
        skills = SkillRegistry.scan(
            package_root=_PACKAGE_SKILLS_DIR,
            user_root=_USER_SKILLS_DIR,
            project_root=ws / ".codey" / "skills",
        )
        session_id = uuid.uuid4().hex[:8]
        store = SessionStore(session_id=session_id)
        store.save_meta(
            workspace=str(ws),
            provider=provider.name,
            started_at=_dt.now().isoformat(timespec="seconds"),
        )

        # --- memory wiring ---
        from ..memory.registry import MemoryRegistry
        from ..memory.store import MemoryStore
        from ..memory.select import pick_relevant
        global_memory_root  = Path.home() / ".config" / "codey" / "memory"
        project_memory_root = ws / ".codey" / "memory"
        memory_registry = MemoryRegistry.scan(
            global_root=global_memory_root, project_root=project_memory_root,
        )
        memory_store = MemoryStore(
            global_root=global_memory_root, project_root=project_memory_root,
        )

        otel_cfg: dict | None = None
        if otel_enabled:
            otel_cfg = {
                "provider_name": provider.name,
                "model": provider.model,
                "base_url": provider.base_url,
            }
        recent_reads: deque = deque(maxlen=5)

        # Forward declaration: the extract hook needs a handle to the Agent
        # so it can read history live + reuse the agent's client. We build
        # the Agent first, then wire the hook factory.
        agent = Agent(
            provider=provider,
            system_prompt=build_system_prompt(skills=skills, memory=memory_registry),
            tools=tools,
            hooks=HookRegistry(),  # placeholder, replaced just below
            session_id=session_id,
            _meta=ui_sinks.meta_writer,
            _recent_reads=recent_reads,
            _session_store=store,
            _memory_registry=memory_registry,
        )

        async def _side_query(user_text: str, registry):
            return await pick_relevant(
                user_text, registry,
                client=agent._client, model=provider.model,
                k=cfg.memory.max_loaded,
            )
        if cfg.memory.side_query:
            agent._memory_select = _side_query

        from ..hooks.builtin import build_memory_extract_hook
        def _memory_extract_factory():
            return build_memory_extract_hook(
                history_provider=lambda: agent.history,
                registry=memory_registry,
                store=memory_store,
                client_provider=lambda: agent._client,
                model=provider.model,
                session_id=session_id,
                queue_path=Path.home() / ".cache" / "codey" / "memory_queue.jsonl",
                meta=ui_sinks.meta_writer,
            )

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
            memory_extract_factory=(
                _memory_extract_factory if cfg.memory.auto_extract else None
            ),
        )
        agent.hooks = hooks

        sess = cls(provider=provider, workspace=ws, cfg=cfg, agent=agent,
                   engine=engine, hooks=hooks, tools=tools,
                   session_id=session_id, skills=skills,
                   memory_registry=memory_registry, memory_store=memory_store,
                   _session_store=store)
        # Capture for build_child_agent (need these to build child hooks).
        sess._ui_approve = ui_sinks.approve
        sess._meta_writer = ui_sinks.meta_writer

        from ..tools.load_skill import LoadSkillTool
        tools.register(LoadSkillTool(skills=skills))

        from ..tools.load_memory import LoadMemoryTool
        tools.register(LoadMemoryTool(registry=memory_registry))

        from ..tools.remember_this import RememberThisTool
        tools.register(RememberThisTool(
            registry=memory_registry, store=memory_store,
            session_id=session_id, default_scope="project",
        ))

        from ..tools.spawn_agent import SpawnAgentTool
        tools.register(SpawnAgentTool(session_provider=lambda: sess))

        from ..tools.compact import CompactTool
        tools.register(CompactTool(session_provider=lambda: sess))

        # Best-effort: drain any leftover extract queue entries from a prior
        # crash. We can't replay the actual extraction (we don't have those
        # messages anymore), but we ack the lines so the queue doesn't grow.
        try:
            from ..memory.queue import ack as _ack, drain as _drain
            qp = Path.home() / ".cache" / "codey" / "memory_queue.jsonl"
            for entry in _drain(qp):
                _ack(qp, line_id=entry.get("_id", ""))
        except Exception:  # noqa: BLE001
            pass
        return sess

    @classmethod
    def build_resumed(
        cls,
        *,
        session_id: str,
        provider_arg: str | None,
        ui_sinks: UISinks,
        workspace: Path | None = None,
        otel_enabled: bool = False,
    ) -> "Session":
        """Load a prior session by id; resume in its workspace + provider."""
        from datetime import datetime as _dt
        from . import history as _history_mod

        store = SessionStore(session_id=session_id)
        meta = store.load_meta()  # raises SessionResumeError if missing
        ws_path = (workspace or Path(meta.workspace)).resolve()
        if not ws_path.is_dir():
            raise SessionResumeError(
                f"workspace {meta.workspace!r} no longer exists; cannot resume"
            )

        cfg = ConfigFile.load()
        try:
            provider = cfg.resolve(provider_arg or meta.provider)
        except RuntimeError as e:
            raise SessionResumeError(
                f"provider {(provider_arg or meta.provider)!r} for session "
                f"{session_id!r} is not usable: {e}"
            ) from e

        engine = PermissionEngine.load(workspace=ws_path)
        tools = build_default_registry()
        skills = SkillRegistry.scan(
            package_root=_PACKAGE_SKILLS_DIR,
            user_root=_USER_SKILLS_DIR,
            project_root=ws_path / ".codey" / "skills",
        )

        from ..memory.registry import MemoryRegistry
        from ..memory.store import MemoryStore
        from ..memory.select import pick_relevant
        global_memory_root  = Path.home() / ".config" / "codey" / "memory"
        project_memory_root = ws_path / ".codey" / "memory"
        memory_registry = MemoryRegistry.scan(
            global_root=global_memory_root, project_root=project_memory_root,
        )
        memory_store = MemoryStore(
            global_root=global_memory_root, project_root=project_memory_root,
        )

        otel_cfg: dict | None = None
        if otel_enabled:
            otel_cfg = {
                "provider_name": provider.name,
                "model": provider.model,
                "base_url": provider.base_url,
            }
        recent_reads: deque = deque(maxlen=5)

        agent = Agent(
            provider=provider,
            system_prompt=build_system_prompt(skills=skills, memory=memory_registry),
            tools=tools,
            hooks=HookRegistry(),  # placeholder, replaced below
            session_id=session_id,
            _meta=ui_sinks.meta_writer,
            _recent_reads=recent_reads,
            _session_store=store,
            _memory_registry=memory_registry,
        )

        async def _side_query(user_text: str, registry):
            return await pick_relevant(
                user_text, registry,
                client=agent._client, model=provider.model,
                k=cfg.memory.max_loaded,
            )
        if cfg.memory.side_query:
            agent._memory_select = _side_query

        from ..hooks.builtin import build_memory_extract_hook
        def _memory_extract_factory():
            return build_memory_extract_hook(
                history_provider=lambda: agent.history,
                registry=memory_registry,
                store=memory_store,
                client_provider=lambda: agent._client,
                model=provider.model,
                session_id=session_id,
                queue_path=Path.home() / ".cache" / "codey" / "memory_queue.jsonl",
                meta=ui_sinks.meta_writer,
            )

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
            memory_extract_factory=(
                _memory_extract_factory if cfg.memory.auto_extract else None
            ),
        )
        agent.hooks = hooks

        # Replace the auto-appended fresh system message with the on-disk
        # history (minus any system entries), then re-prepend the fresh one.
        loaded = store.load_history()
        loaded_non_system = [m for m in loaded if m.role != "system"]
        agent.history = [agent.history[0]] + loaded_non_system
        _history_mod.repair(agent.history)

        sess = cls(
            provider=provider, workspace=ws_path, cfg=cfg, agent=agent,
            engine=engine, hooks=hooks, tools=tools,
            session_id=session_id, skills=skills,
            memory_registry=memory_registry, memory_store=memory_store,
            _session_store=store,
        )
        sess._ui_approve = ui_sinks.approve
        sess._meta_writer = ui_sinks.meta_writer

        from ..tools.load_skill import LoadSkillTool
        tools.register(LoadSkillTool(skills=skills))
        from ..tools.load_memory import LoadMemoryTool
        tools.register(LoadMemoryTool(registry=memory_registry))
        from ..tools.remember_this import RememberThisTool
        tools.register(RememberThisTool(
            registry=memory_registry, store=memory_store,
            session_id=session_id, default_scope="project",
        ))
        from ..tools.spawn_agent import SpawnAgentTool
        tools.register(SpawnAgentTool(session_provider=lambda: sess))
        from ..tools.compact import CompactTool
        tools.register(CompactTool(session_provider=lambda: sess))

        store.touch_meta(
            last_at=_dt.now().isoformat(timespec="seconds"),
            message_count=len(agent.history),
        )
        return sess

    async def swap_provider(self, name: str) -> Provider:
        new_provider = self.cfg.resolve(name)
        await self.agent.swap_provider(new_provider)
        self.provider = new_provider
        return new_provider

    def build_child_agent(
        self,
        *,
        description: str,
        provider_name: str | None = None,
    ) -> tuple[Agent, str]:
        """Construct a sub-agent. The ONLY place that knows how.

        Returns (child_agent, child_session_id). The caller (SpawnAgentTool)
        is responsible for draining the child's run() and calling
        child.aclose() when done.
        """
        from ..hooks.builtin import build_child_hooks
        from ..prompt import build_subagent_system_prompt

        child_provider = self.provider if provider_name is None else self.cfg.resolve(provider_name)

        self._sub_counter += 1
        child_id = f"{self.session_id}.sub.{self._sub_counter}"

        EXCLUDED_FROM_CHILD = {"spawn_agent", "todo_write", "compact", "remember_this"}
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
            memory=self.memory_registry,
        )

        child = Agent(
            provider=child_provider,
            system_prompt=child_system,
            tools=child_tools,
            hooks=child_hooks,
            session_id=child_id,
            _meta=self._meta_writer,
            _recent_reads=child_recent_reads,
            context_thresholds=_context_pipeline.CHILD_THRESHOLDS,
            _memory_registry=self.memory_registry,  # children see the index, can load_memory
        )
        return child, child_id

    async def aclose(self) -> None:
        await self.agent.aclose()
