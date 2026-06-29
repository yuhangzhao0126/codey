"""Tests for the /compact slash command."""
from __future__ import annotations

import pytest
from textual.widgets import Input

from codey.core.turn import Agent
from codey.ui import CodeyApp
from codey.ui.slash_commands import build_slash_commands

pytestmark = pytest.mark.usefixtures("temp_config")


def test_compact_command_is_registered():
    cmds = build_slash_commands()
    assert "compact" in cmds
    help_text = cmds["compact"].help.lower()
    assert "compact" in help_text or "summar" in help_text


async def _submit(pilot, text: str) -> None:
    inp = pilot.app.query_one(Input)
    inp.focus()
    inp.value = text
    await pilot.pause()
    await inp.action_submit()
    await pilot.pause()


async def test_compact_command_calls_compact_now(monkeypatch):
    called = []

    async def fake_compact_now(self):
        called.append(True)

    monkeypatch.setattr(Agent, "compact_now", fake_compact_now)

    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/compact")
    assert called == [True]
