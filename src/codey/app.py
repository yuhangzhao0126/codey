"""Single entry point for codey. Launches the Textual UI."""

from __future__ import annotations

import argparse

from .ui.app import CodeyApp


def main() -> None:
    parser = argparse.ArgumentParser(prog="codey", description="codey — a coding agent")
    parser.add_argument(
        "--profile", "-p",
        help="profile name from ~/.config/codey/config.toml (overrides $CODEY_PROFILE)",
    )
    args = parser.parse_args()
    CodeyApp(profile_arg=args.profile).run()


if __name__ == "__main__":
    main()
