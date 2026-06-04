"""Floating list of matching slash commands, anchored above the input.

Visible only while the user is typing a slash command (line starts with '/'
and there is no space yet — once they start typing args, we hide).
"""

from __future__ import annotations

from textual.widgets import OptionList


class SlashSuggest(OptionList):
    DEFAULT_CSS = """
    SlashSuggest {
        layer: overlay;
        dock: bottom;
        offset: 0 -3;            /* sit just above the input row */
        height: auto;
        max-height: 8;
        width: 60;
        margin: 0 1;
        background: $panel;
        border: round $primary;
        display: none;
    }
    SlashSuggest:focus { border: round $accent; }
    """
