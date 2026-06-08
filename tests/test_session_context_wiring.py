"""Verify Session.build wires every context-related piece onto the Agent."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

from codey.core.session import Session


class FakeSinks:
    def __init__(self):
        self.transcript_writer = None
        self.meta_lines = []
        self.meta_writer = self.meta_lines.append
        self.todo_writer = None

    async def approve(self, ctx):
        from codey.permissions import Verdict
        return Verdict.allow_once()


def test_agent_session_id_matches_session(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert sess.agent.session_id == sess.session_id
    assert len(sess.session_id) == 8


def test_agent_meta_is_wired(temp_config, tmp_path: Path):
    sinks = FakeSinks()
    sess = Session.build(profile_arg="alpha", ui_sinks=sinks, workspace=tmp_path)
    sess.agent._meta("[ctx: test]")
    assert "[ctx: test]" in sinks.meta_lines


def test_agent_recent_reads_is_deque(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert isinstance(sess.agent._recent_reads, deque)
    assert sess.agent._recent_reads.maxlen == 5


def test_compact_tool_registered(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert "compact" in sess.tools.tools


def test_child_does_not_get_compact_tool(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    child, _ = sess.build_child_agent(description="probe")
    assert "compact" not in child.tools.tools


def test_child_gets_own_recent_reads_deque(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    child, _ = sess.build_child_agent(description="probe")
    assert isinstance(child._recent_reads, deque)
    assert child._recent_reads is not sess.agent._recent_reads


def test_recent_reads_hook_registered_in_default_hooks(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    names = [h.name for h in sess.hooks.list()]
    assert "recent_reads" in names
