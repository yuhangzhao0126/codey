"""Pattern suggestion helper for the "always allow / always deny" UX.

Given a concrete tool + arg_str (e.g. `bash` + `git status -sb`), suggest a
glob the user can save as a rule (`git status*`).
"""

from __future__ import annotations

from pathlib import Path


def suggest_pattern(tool: str, arg_str: str) -> str:
    """Derive a reasonable starter pattern from a concrete command/path."""
    if tool != "bash":
        # For file tools, suggest a dir glob.
        path = Path(arg_str).expanduser()
        parent = str(path.parent)
        return f"{parent}/*" if parent not in ("", ".") else "*"
    # bash: keep the first 1-2 tokens, then *
    tokens = arg_str.split()
    if not tokens:
        return "*"
    if len(tokens) == 1:
        return f"{tokens[0]}*"
    # `git status -sb` -> `git status*`; `npm test --watch` -> `npm test*`
    return f"{tokens[0]} {tokens[1]}*"
