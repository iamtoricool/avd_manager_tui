"""Log pane.

The pane is the busiest widget in the app: a 2 GB package install can emit
thousands of lines. To stay responsive we never write one line per refresh --
:func:`LogPane.push` only appends to a buffer, and a timer flushes the buffer
to the widget at a fixed cadence.

The pane has no header of its own: the divider above it carries the ``Output``
label and is the single bar that resizes and collapses the pane.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog

#: Seconds between buffer flushes.
FLUSH_INTERVAL = 0.1

#: Cap on buffered lines so a runaway process cannot exhaust memory.
MAX_BUFFER = 5000


class LogPane(Vertical):
    """Bottom pane showing command output."""

    DEFAULT_CSS = """
    LogPane { height: 14; min-height: 3; }
    LogPane.-hidden { display: none; }
    LogPane RichLog { height: 1fr; border: none; background: $boost; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._buffer: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield RichLog(id="log", markup=True, max_lines=MAX_BUFFER * 2, highlight=False)

    def on_mount(self) -> None:
        self.set_interval(FLUSH_INTERVAL, self.flush)

    # ------------------------------------------------------------------ #

    def push(self, text: str, kind: str = "out") -> None:
        """Queue a line for display. Safe to call at high frequency."""
        self._buffer.append((text, kind))
        if len(self._buffer) > MAX_BUFFER:
            del self._buffer[: len(self._buffer) - MAX_BUFFER]

    def push_many(self, lines) -> None:
        for text, kind in lines:
            self.push(text, kind)

    def flush(self) -> None:
        """Write buffered lines to the widget in a single batch."""
        if not self._buffer:
            return
        pending, self._buffer = self._buffer, []
        log = self.query_one("#log", RichLog)
        for text, kind in pending:
            if not text:
                continue
            log.write(f"[{_css_class(kind)}]{text}[/]\n")
        log.auto_scroll = True

    def clear(self) -> None:
        self._buffer.clear()
        self.query_one("#log", RichLog).clear()

    def set_hidden(self, hidden: bool) -> None:
        self.set_class(hidden, "-hidden")

    def toggle(self) -> None:
        self.set_hidden(not self.has_class("-hidden"))


def _css_class(kind: str) -> str:
    return {
        "err": "log-line-err",
        "info": "log-line-info",
        "cmd": "log-line-cmd",
    }.get(kind, "log-line-out")
