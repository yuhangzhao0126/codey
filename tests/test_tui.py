"""TUI integration tests driven by Textual's Pilot."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Input

from codey.agent import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    Message,
    TurnCompleted,
    TurnStarted,
)
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
        for cmd in ("/exit", "/help", "/reset", "/model",
                    "/profile", "/profiles", "/permission"):
            assert cmd in text, f"{cmd} missing from /help output: {text!r}"


# ---------- /model — reflects active profile, follows runtime swaps ----------

async def test_model_shows_active_profile_and_follows_switch():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/model")
        text = _transcript_text(app)
        assert "alpha" in text
        assert "alpha-model" in text
        assert "example.com/alpha/v1" in text
        # Switch and ask again — output must reflect the new profile.
        await _submit(pilot, "/profile beta")
        for _ in range(10):
            if app.agent.profile.name == "beta":
                break
            await pilot.pause()
        await _submit(pilot, "/model")
        text = _transcript_text(app)
        assert "beta-model" in text
        assert "example.com/beta/v1" in text


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


# ---------- busy-state behavior (1-3 from busy/cancel design) ----------

class _SlowAgentRun:
    """Stand-in for Agent.run that yields one TurnStarted then sleeps until
    cancelled. Lets us test busy-state UI without a real LLM call."""

    def __init__(self, *_, **__):
        self.cancelled = False

    def __call__(self, _user_input: str):
        return self._gen()

    async def _gen(self):
        yield TurnStarted()
        try:
            await asyncio.sleep(10)  # cancelled by the test
            yield AssistantMessageCompleted(text="should never happen")
            yield TurnCompleted(reason="stop")
        except (asyncio.CancelledError, GeneratorExit):
            self.cancelled = True
            # Mirror what the real agent does on cancellation.
            yield TurnCompleted(reason="cancelled")
            raise


async def test_busy_placeholder_changes_while_turn_in_flight(monkeypatch):
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        slow = _SlowAgentRun()
        monkeypatch.setattr(app.agent, "run", slow)
        inp = app.query_one(Input)
        assert inp.placeholder == CodeyApp.IDLE_PLACEHOLDER

        await _submit(pilot, "hello")
        # Let the worker tick so _set_busy(True) runs.
        for _ in range(10):
            if app._busy:
                break
            await pilot.pause()
        assert app._busy is True
        assert inp.placeholder == CodeyApp.BUSY_PLACEHOLDER

        # Cancel and confirm placeholder flips back.
        assert app._cancel_current_turn() is True
        for _ in range(20):
            if not app._busy:
                break
            await pilot.pause()
        assert app._busy is False
        assert inp.placeholder == CodeyApp.IDLE_PLACEHOLDER


async def test_second_submit_while_busy_is_announced(monkeypatch):
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        slow = _SlowAgentRun()
        monkeypatch.setattr(app.agent, "run", slow)

        await _submit(pilot, "first")
        for _ in range(10):
            if app._busy:
                break
            await pilot.pause()
        assert app._busy is True

        # Second submit should NOT enqueue or send; it should log a friendly note
        # and preserve the input value the user has typed.
        inp = app.query_one(Input)
        inp.focus()
        inp.value = "second"
        await inp.action_submit()
        await pilot.pause()

        text = _transcript_text(app).lower()
        assert "busy" in text, text
        # The user's typed second message must not have been cleared.
        assert inp.value == "second"

        # Clean up.
        app._cancel_current_turn()
        for _ in range(20):
            if not app._busy:
                break
            await pilot.pause()


async def test_escape_cancels_in_flight_turn(monkeypatch):
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        slow = _SlowAgentRun()
        monkeypatch.setattr(app.agent, "run", slow)

        await _submit(pilot, "go")
        for _ in range(10):
            if app._busy:
                break
            await pilot.pause()
        assert app._busy is True

        await pilot.press("escape")
        for _ in range(20):
            if not app._busy:
                break
            await pilot.pause()
        assert app._busy is False
        assert slow.cancelled is True


# ---------- permission slash command + mode in header ----------

async def test_permission_status_shows_mode_and_counts():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/permission status")
        text = _transcript_text(app)
        assert "mode" in text.lower()
        assert "safe" in text.lower()


async def test_permission_bare_opens_mode_picker():
    from codey.tui import ModePickerScreen
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/permission")
        for _ in range(10):
            if any(isinstance(s, ModePickerScreen) for s in app.screen_stack):
                break
            await pilot.pause()
        assert any(isinstance(s, ModePickerScreen) for s in app.screen_stack), \
            [type(s).__name__ for s in app.screen_stack]


async def test_permission_picker_switch_to_read_only(tmp_path, monkeypatch):
    import codey.permissions as perms
    monkeypatch.setattr(perms, "USER_PERMISSIONS_PATH", tmp_path / "permissions.toml")
    monkeypatch.setattr(perms, "PROJECT_PERMISSIONS_PATH", tmp_path / "_project.toml")
    from codey.permissions import Mode
    from codey.tui import ModePickerScreen
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/permission")
        for _ in range(10):
            if any(isinstance(s, ModePickerScreen) for s in app.screen_stack):
                break
            await pilot.pause()
        # ORDER is [paranoid, read-only, safe, yolo]; SAFE is index 2 (active),
        # so move up once to land on read-only (index 1).
        await pilot.press("up")
        await pilot.press("enter")
        for _ in range(10):
            if app.engine.mode == Mode.READ_ONLY:
                break
            await pilot.pause()
        assert app.engine.mode == Mode.READ_ONLY


async def test_permission_mode_switch_updates_header(tmp_path, monkeypatch):
    # Point the user-permissions file at tmp so save_mode doesn't touch real config.
    import codey.permissions as perms
    monkeypatch.setattr(perms, "USER_PERMISSIONS_PATH", tmp_path / "permissions.toml")
    monkeypatch.setattr(perms, "PROJECT_PERMISSIONS_PATH", tmp_path / "_project.toml")
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/permission mode read-only")
        await pilot.pause()
        from codey.permissions import Mode
        assert app.engine.mode == Mode.READ_ONLY
        assert "READ-ONLY" in app.sub_title or "read-only" in app.sub_title.lower()


async def test_permission_unknown_subcommand_reports_error():
    app = CodeyApp(profile_arg=None)
    async with app.run_test() as pilot:
        await _submit(pilot, "/permission frobnicate")
        text = _transcript_text(app).lower()
        assert "unknown subcommand" in text
