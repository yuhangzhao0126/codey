"""TUI test for /subs panel and SubAgentPanelScreen."""

from __future__ import annotations

import pytest

from textual.widgets import Input, OptionList, RichLog, Static

from codey.core.events import (
    AssistantMessageCompleted,
    RoundStarted,
    TurnCompleted,
    TurnStarted,
)
from codey.ui import CodeyApp, SubAgentPanelScreen

pytestmark = pytest.mark.usefixtures("temp_config")


async def _submit(pilot, text: str) -> None:
    inp = pilot.app.query_one(Input)
    inp.focus()
    inp.value = text
    await pilot.pause()
    await inp.action_submit()
    await pilot.pause()


def _seed_recorder(app: CodeyApp) -> None:
    """Hand-feed the recorder with two children: one done, one running."""
    rec = app.session.subagent_recorder
    a = f"{app.session.session_id}.sub.1"
    b = f"{app.session.session_id}.sub.2"
    rec.set_label(a, "investigate-auth")
    rec.set_label(b, "investigate-db")

    for ev in [TurnStarted(), RoundStarted(round=0), RoundStarted(round=1),
               AssistantMessageCompleted(text="done"),
               TurnCompleted(reason="stop")]:
        rec.append(a, ev)

    for ev in [TurnStarted(), RoundStarted(round=0)]:
        rec.append(b, ev)


async def test_subs_opens_panel_with_recorded_children():
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        _seed_recorder(app)
        await _submit(pilot, "/subs")
        # Wait for the worker to mount the modal.
        for _ in range(20):
            if isinstance(pilot.app.screen, SubAgentPanelScreen):
                break
            await pilot.pause()
        assert isinstance(pilot.app.screen, SubAgentPanelScreen)

        screen = pilot.app.screen
        ol = screen.query_one("#subs-list", OptionList)
        # Both children appear as options.
        assert ol.option_count == 2
        # Render labels include the descriptions.
        labels = "\n".join(
            ol.get_option_at_index(i).prompt.plain if hasattr(ol.get_option_at_index(i).prompt, "plain")
            else str(ol.get_option_at_index(i).prompt)
            for i in range(ol.option_count)
        )
        assert "investigate-auth" in labels
        assert "investigate-db" in labels
        assert "done" in labels
        assert "running" in labels
        # 2 rounds shows up for the completed child.
        assert "2 rounds" in labels

        # Esc closes.
        await pilot.press("escape")
        for _ in range(10):
            if not isinstance(pilot.app.screen, SubAgentPanelScreen):
                break
            await pilot.pause()
        assert not isinstance(pilot.app.screen, SubAgentPanelScreen)


async def test_subs_panel_empty_state_shows_placeholder():
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/subs")
        for _ in range(20):
            if isinstance(pilot.app.screen, SubAgentPanelScreen):
                break
            await pilot.pause()
        assert isinstance(pilot.app.screen, SubAgentPanelScreen)

        screen = pilot.app.screen
        # Empty state writes a Static with "(no sub-agents spawned yet)".
        # Verify no OptionList was mounted.
        assert not screen.query(OptionList)
        text = "\n".join(str(s.render()) for s in screen.query(Static))
        assert "no sub-agents" in text


async def test_subs_panel_detail_view_renders_event_lines():
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        _seed_recorder(app)
        await _submit(pilot, "/subs")
        for _ in range(20):
            if isinstance(pilot.app.screen, SubAgentPanelScreen):
                break
            await pilot.pause()
        screen = pilot.app.screen

        # Drill into the first child by directly invoking the panel API
        # (clicking through OptionList from a Pilot test is flakier).
        screen._show_detail(screen.child_ids[0])
        # Detail view mounts a RichLog and writes one line per event.
        # Give Textual several pump cycles to render the strips so we can
        # inspect log.lines.
        for _ in range(5):
            await pilot.pause()

        log = screen.query_one("#subs-detail", RichLog)
        # The log should have one line per event.
        assert len(log.lines) >= 4  # turn started + 2 rounds + msg + finished


async def test_approval_modal_shows_requester_when_provided():
    """When the permission hook supplies a requester, the modal renders
    a 'requested by:' line. Parent calls (requester=None) don't get it."""
    from codey.ui import ApprovalScreen
    app = CodeyApp(provider_arg=None)
    async with app.run_test() as pilot:
        # With requester:
        await pilot.app.push_screen(
            ApprovalScreen(
                tool="bash", command="ls", reason="paranoid",
                requester='sub-agent[2] "investigate-db"',
            )
        )
        await pilot.pause()
        screen = pilot.app.screen
        joined = "\n".join(str(s.render()) for s in screen.query(Static))
        assert "requested by" in joined
        assert "investigate-db" in joined
        await pilot.press("escape")
        await pilot.pause()

        # Without requester:
        await pilot.app.push_screen(
            ApprovalScreen(tool="bash", command="ls", reason="paranoid")
        )
        await pilot.pause()
        screen = pilot.app.screen
        joined = "\n".join(str(s.render()) for s in screen.query(Static))
        assert "requested by" not in joined

