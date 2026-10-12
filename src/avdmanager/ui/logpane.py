"""Log pane.

The pane is the busiest widget in the app: a 2 GB package install can emit
thousands of lines. To stay responsive we never write one line per refresh --
:func:`LogPane.push` only appends to a buffer, and a timer flushes the buffer
to the widget at a fixed cadence.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog, Static

#: Seconds between buffer flushes.
FLUSH_INTERVAL = 0.1

#: Cap on buffered lines so a runaway process cannot exhaust memory.
MAX_BUFFER = 5000


class LogPane(Vertical):
    """Bottom pane showing command output."""

    DEFAULT_CSS = """
    LogPane { height: 14; min-height: 3; border-top: solid $primary; }
    LogPane.-hidden { display: none; }
    LogPane > .log-title { dock: top; height: 1; padding: 0 1;
                           background: $panel; color: $text-muted; }
    LogPane RichLog { height: 1fr; border: none; background: $boost; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._buffer: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Static("Output", classes="log-title", id="log-title")
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

    def title(self, text: str) -> None:
        try:
            self.query_one(".log-title", Static).update(text)
        except Exception:  # pragma: no cover - pane not mounted yet
            pass

    def clear(self) -> None:
        self._buffer.clear()
        self.query_one("#log", RichLog).clear()

    def toggle(self) -> None:
        self.set_class(not self.has_class("-hidden"), "-hidden")


def _css_class(kind: str) -> str:
    return {
        "err": "log-line-err",
        "info": "log-line-info",
        "cmd": "log-line-cmd",
    }.get(kind, "log-line-out")