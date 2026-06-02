"""TUI integration tests driven by Textual's Pilot."""

from __future__ import annotations

import pytest
from textual.widgets import Input

from codey.agent import Message
from codey.tui import ApprovalScreen, CodeyApp, ProfilePickerScreen, SlashSuggest

pytestmark = pytest.mark.usefixtures("temp_config")


def _transcript_text(app: CodeyApp) -> str:
    """Flatten RichLog content to a string for substring assertions."""
    log = app.transcript
    parts: list[str] = []
    for line in log.lines:
        try:
            parts.append(line.text)
        except AttributeError:
            parts.append(str(line))
    return "\n".join(parts)


async def _submit(pilot, text: str) -> None:
    """Set the input's value and submit it as if Enter were pressed."""
    inp = pilot.app.query_one(Input)
    inp.focus()
    inp.value = text
    await pilot.pause()
    # Calling action_submit() is what Input does internally on Enter.
    await inp.action_submit()
    await pilot.pause()


def _display_is(widget, value: str) -> bool:
    """display style can be a str or an enum-like in different Textual versions."""
    d = widget.styles.display
    return getattr(d, "value", d) == value


# ---------- /help ----------

async def test_help_lists_all_commands():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/help")
        text = _transcript_text(app)
        for cmd in ("/exit", "/help", "/reset", "/profile", "/profiles"):
            assert cmd in text, f"{cmd} missing from /help output: {text!r}"


# ---------- /reset ----------

async def test_reset_clears_history_but_keeps_system():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        app.agent.history.append(Message(role="user", content="hi"))
        app.agent.history.append(Message(role="assistant", content="hello"))
        await _submit(pilot, "/reset")
        roles = [m.role for m in app.agent.history]
        assert roles == ["system"], f"expected just system msg, got {roles}"
        assert "history cleared" in _transcript_text(app)


# ---------- /profiles ----------

async def test_profiles_lists_each_profile():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/profiles")
        text = _transcript_text(app)
        for name in ("alpha", "beta"):
            assert name in text


# ---------- /profile NAME ----------

async def test_profile_direct_switch():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        assert app.agent.profile.name == "alpha"
        await _submit(pilot, "/profile beta")
        # swap_profile is async via lambda -> worker; give it a tick.
        for _ in range(10):
            if app.agent.profile.name == "beta":
                break
            await pilot.pause()
        assert app.agent.profile.name == "beta"
        assert app.agent.profile.model == "beta-model"
        assert "beta" in app.sub_title


# ---------- /profile (no arg) opens picker ----------

async def test_profile_picker_opens_and_switches():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        assert app.agent.profile.name == "alpha"
        await _submit(pilot, "/profile")
        await pilot.pause()
        assert any(isinstance(s, ProfilePickerScreen) for s in app.screen_stack), \
            f"picker not on stack: {[type(s).__name__ for s in app.screen_stack]}"
        await pilot.press("down")
        await pilot.press("enter")
        for _ in range(10):
            if app.agent.profile.name == "beta":
                break
            await pilot.pause()
        assert app.agent.profile.name == "beta"


# ---------- /exit ----------

async def test_exit_quits_app():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/exit")
        await pilot.pause()
        assert not app.is_running


# ---------- substring resolve ----------

async def test_pro_is_ambiguous_and_logged_as_error():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/pro")
        text = _transcript_text(app).lower()
        assert "ambiguous" in text, text


async def test_exi_resolves_to_exit():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/exi")
        await pilot.pause()
        assert not app.is_running


# ---------- slash dropdown ----------

async def test_slash_dropdown_appears_and_filters():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        inp = app.query_one(Input)
        inp.focus()
        inp.value = "/pro"
        # Fire the on_input_changed handler explicitly since setting .value
        # programmatically doesn't always emit Input.Changed during tests.
        app.on_input_changed(Input.Changed(inp, "/pro"))
        await pilot.pause()
        suggest = app.query_one(SlashSuggest)
        assert not _display_is(suggest, "none"), \
            f"dropdown hidden: {suggest.styles.display!r}"
        assert suggest.option_count == 2, suggest.option_count


async def test_slash_dropdown_hides_when_no_match():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        inp = app.query_one(Input)
        inp.focus()
        inp.value = "/zzz"
        app.on_input_changed(Input.Changed(inp, "/zzz"))
        await pilot.pause()
        suggest = app.query_one(SlashSuggest)
        assert _display_is(suggest, "none"), \
            f"dropdown should be hidden but is: {suggest.styles.display!r}"


# ---------- ctrl+r hotkey ----------

async def test_ctrl_r_clears_history():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        app.agent.history.append(Message(role="user", content="hi"))
        await pilot.press("ctrl+r")
        await pilot.pause()
        roles = [m.role for m in app.agent.history]
        assert roles == ["system"], roles


# ---------- ctrl+p hotkey ----------

async def test_ctrl_p_opens_picker():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+p")
        await pilot.pause()
        # Picker may take an extra tick to push.
        for _ in range(10):
            if any(isinstance(s, ProfilePickerScreen) for s in app.screen_stack):
                break
            await pilot.pause()
        assert any(isinstance(s, ProfilePickerScreen) for s in app.screen_stack), \
            [type(s).__name__ for s in app.screen_stack]


# ---------- bash tool readonly path (no UI) ----------

async def test_bash_readonly_runs_without_modal():
    from codey.tools.bash import BashTool
    tool = BashTool(approve=None)
    out = await tool.run({"command": "echo hello"})
    assert "hello" in out
    assert "exit=0" in out
