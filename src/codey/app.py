"""Single entry point for codey. Launches the Textual UI."""

from __future__ import annotations

import argparse

from .ui.app import CodeyApp


def main() -> None:
    from .hooks.builtin import otel_enabled as _otel_env_enabled
    parser = argparse.ArgumentParser(prog="codey", description="codey — a coding agent")
    parser.add_argument(
        "--profile", "-p",
        help="profile name from ~/.config/codey/config.toml (overrides $CODEY_PROFILE)",
    )
    parser.add_argument(
        "--resume", "-r",
        nargs="?",
        const="__PICK__",
        default=None,
        metavar="SID",
        help="resume a prior session in the current workspace. With no arg, "
             "open a picker; with SID, resume that session directly.",
    )
    parser.add_argument(
        "--otel", action="store_true",
        help="emit OpenTelemetry spans for every turn + tool call "
             "(requires: uv sync --extra observability)",
    )
    args = parser.parse_args()
    otel_on = args.otel or _otel_env_enabled()
    CodeyApp(profile_arg=args.profile, resume_arg=args.resume, otel=otel_on).run()


if __name__ == "__main__":
    main()
