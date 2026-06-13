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


def test_child_uses_child_thresholds(temp_config, tmp_path: Path):
    from codey import context as context_pipeline
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    child, _ = sess.build_child_agent(description="probe")
    assert child.context_thresholds is context_pipeline.CHILD_THRESHOLDS
    assert sess.agent.context_thresholds is context_pipeline.PARENT_THRESHOLDS


# -- [memory] config toggles gate behavior on the built Session --


def _write_memory_cfg(tmp_path: Path, monkeypatch, block: str) -> None:
    """Rewrite the temp config.toml (already pointed at by temp_config) to
    include a [memory] block."""
    from codey import config as config_mod
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'default_profile = "alpha"\n\n'
        "[profiles.alpha]\n"
        'base_url = "https://example.com/alpha/v1"\n'
        'api_key  = "sk-alpha"\n'
        'model    = "alpha-model"\n'
        + block
    )
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)


def test_side_query_on_by_default(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert sess.agent._memory_select is not None


def test_side_query_disabled_leaves_memory_select_none(temp_config, tmp_path: Path, monkeypatch):
    _write_memory_cfg(tmp_path, monkeypatch, "\n[memory]\nside_query = false\n")
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    assert sess.agent._memory_select is None


def test_auto_extract_on_by_default_registers_hook(temp_config, tmp_path: Path):
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    names = [h.name for h in sess.hooks.list()]
    assert "memory_extract" in names


def test_auto_extract_disabled_skips_hook(temp_config, tmp_path: Path, monkeypatch):
    _write_memory_cfg(tmp_path, monkeypatch, "\n[memory]\nauto_extract = false\n")
    sess = Session.build(profile_arg="alpha", ui_sinks=FakeSinks(), workspace=tmp_path)
    names = [h.name for h in sess.hooks.list()]
    assert "memory_extract" not in names
