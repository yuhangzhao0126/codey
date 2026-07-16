"""Single entry point for codey. Launches the Textual UI, or headless with -p."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys

CODEY_UPDATE_REF = "git+https://github.com/yuhangzhao0126/codey@main"


def _run_update() -> int:
    """Reinstall codey from GitHub main via uv. Returns the exit code.

    codey is installed as a uv tool, so updating means re-running
    `uv tool install --force <repo@main>` — the same thing install.sh does.
    Your config in ~/.config/codey/ is never touched.
    """
    if shutil.which("uv") is None:
        print(
            "codey: uv is required to self-update but was not found on PATH.\n"
            "  install it: curl -LsSf https://astral.sh/uv/install.sh | sh",
            file=sys.stderr,
        )
        return 1

    cmd = ["uv", "tool", "install", "--force", CODEY_UPDATE_REF]
    print(f"codey: updating from {CODEY_UPDATE_REF}")
    print(f"codey: running: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd)
    except OSError as e:
        print(f"codey: update failed to launch uv: {e}", file=sys.stderr)
        return 1
    if proc.returncode == 0:
        print("codey: update complete. run `codey` to use the new version.")
    else:
        print(f"codey: update failed (uv exited {proc.returncode}).", file=sys.stderr)
    return proc.returncode


def main() -> None:
    from .hooks.builtin import otel_enabled as _otel_env_enabled
    parser = argparse.ArgumentParser(prog="codey", description="codey — a coding agent")
    parser.add_argument(
        "--prompt", "-p",
        help="run one prompt headless (no TUI), print result, exit — for eval/scripting",
    )
    parser.add_argument(
        "--provider",
        help="provider name from ~/.config/codey/config.toml (overrides $CODEY_PROVIDER)",
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
    parser.add_argument(
        "--update", action="store_true",
        help="update codey to the latest version from GitHub main, then exit",
    )
    args = parser.parse_args()
    if args.update:
        sys.exit(_run_update())
    otel_on = args.otel or _otel_env_enabled()
    if args.prompt:
        from .headless import run_headless
        sys.exit(asyncio.run(run_headless(args.prompt, provider_arg=args.provider, otel=otel_on)))
    from .ui.app import CodeyApp
    CodeyApp(provider_arg=args.provider, resume_arg=args.resume, otel=otel_on).run()


if __name__ == "__main__":
    main()
