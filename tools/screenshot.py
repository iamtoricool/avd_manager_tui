#!/usr/bin/env python3
"""Render a screenshot of the running app.

Useful for reviewing layout changes without launching a terminal by hand.

    make shot          # writes docs/screenshot.svg
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from avdmanager.context import build_context
from avdmanager.runner import JobRunner
from avdmanager.ui.app import AvdManagerApp

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshot.svg"

#: Seconds to let the background loaders finish.
SETTLE_SECONDS = 8.0


async def capture(size: tuple[int, int] = (150, 45)) -> Path:
    context = build_context(runner=JobRunner())
    app = AvdManagerApp(context)
    async with app.run_test(size=size) as pilot:
        deadline = SETTLE_SECONDS * 10
        while deadline > 0:
            await pilot.pause(0.1)
            deadline -= 1
        OUT.parent.mkdir(parents=True, exist_ok=True)
        app.save_screenshot(str(OUT))
        app.exit()
    return OUT


def main() -> int:
    path = asyncio.run(capture())
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())