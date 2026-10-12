"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from avdmanager import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="avdmanager",
        description="Terminal UI for managing the Android SDK, AVDs and emulators.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help="use an alternative config file (default: $XDG_CONFIG_HOME/avdmanager/config.toml)",
    )
    parser.add_argument(
        "--sdk-path",
        metavar="DIR",
        help="override the Android SDK root for this run",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="use the legacy sdkmanager instead of the modern android CLI",
    )
    parser.add_argument("--version", action="version", version=f"avdmanager {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from avdmanager.context import build_context

    overrides: dict = {}
    if args.sdk_path:
        overrides["sdk_path"] = args.sdk_path
    if args.legacy:
        overrides["prefer_android_cli"] = False

    context = build_context(args.config)
    if overrides:
        context.config.__dict__.update(overrides)
        context.rebuild()

    if not context.ready:
        # Still start: the UI explains what is missing and links to the fix.
        pass

    from avdmanager.ui.app import AvdManagerApp

    app = AvdManagerApp(context)
    app.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())