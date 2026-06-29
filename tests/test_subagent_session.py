"""Tests for Session.build_child_agent and SubAgentRecorder."""

from __future__ import annotations

from pathlib import Path

import pytest

from codey.core.session import Session, SubAgentRecorder
from codey.ui.renderers import UISinks


def _build_session(tmp_path: Path) -> Session:
    """Build a Session with a temp workspace and a no-op UI sinks bundle."""
    sinks = UISinks(
        meta_writer=lambda _t: None,
        approve=None,
        todo_writer=None,
    )
    return Session.build(
        provider_arg=None,
        ui_sinks=sinks,
        workspace=tmp_path,
        otel_enabled=False,
    )


def test_recorder_caps_events_per_child():
    """The recorder is per-session, bounded per child, snapshot-on-read."""
    rec = SubAgentRecorder(per_child_cap=3)
    rec.append("sess.sub.1", "event-a")
    rec.append("sess.sub.1", "event-b")
    rec.append("sess.sub.1", "event-c")
    rec.append("sess.sub.1", "event-d")  # exceeds cap

    events = rec.events_for("sess.sub.1")
    assert len(events) == 3
    # Cap-by-drop-oldest, so most recent three survive.
    assert events == ["event-b", "event-c", "event-d"]


def test_recorder_lists_children_in_insertion_order():
    rec = SubAgentRecorder()
    rec.append("sess.sub.1", "x")
    rec.append("sess.sub.2", "y")
    rec.append("sess.sub.1", "z")  # already-seen child
    assert rec.children() == ["sess.sub.1", "sess.sub.2"]


async def test_build_child_agent_shape(temp_config, tmp_path):
    sess = _build_session(tmp_path)
    child, child_id = sess.build_child_agent(description="probe auth")
    try:
        # Child id format: <parent>.sub.<n>, starting at 1.
        assert child_id == f"{sess.session_id}.sub.1"

        # Counter persists across calls.
        child2, child_id_2 = sess.build_child_agent(description="probe db")
        try:
            assert child_id_2 == f"{sess.session_id}.sub.2"
        finally:
            await child2.aclose()

        # Tool registry: parent's tools minus spawn_agent and todo_write.
        # spawn_agent isn't registered yet at this point in the build sequence,
        # so we only assert todo_write is gone and that the rest survived.
        assert "todo_write" not in child.tools.tools
        assert "bash" in child.tools.tools
        assert "read_file" in child.tools.tools

        # System prompt reflects the description.
        assert "probe auth" in child.history[0].content
    finally:
        await child.aclose()
        await sess.aclose()


async def test_build_child_agent_resolves_named_provider(temp_config, tmp_path):
    sess = _build_session(tmp_path)
    child, _ = sess.build_child_agent(description="x", provider_name="beta")
    try:
        assert child.provider.name == "beta"
    finally:
        await child.aclose()
        await sess.aclose()


async def test_build_child_agent_unknown_provider_raises(temp_config, tmp_path):
    sess = _build_session(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            # cfg.resolve raises RuntimeError on unknown — surface as-is.
            sess.build_child_agent(description="x", provider_name="ghost-provider")
    finally:
        await sess.aclose()
