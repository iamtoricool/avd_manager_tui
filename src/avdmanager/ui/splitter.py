"""Draggable pane dividers.

Textual has no built-in splitter widget, so this is a thin one: a 1-cell rule
that captures the mouse while held and reports the drag delta. The app decides
what a delta means (sidebar width vs log height) and clamps the result.

A divider may carry a label (the log's ``▼ Output`` header) and a click action,
so one bar can both resize its pane and collapse it: a drag reports deltas, a
click without movement fires ``on_click``.
"""

from __future__ import annotations

from collections.abc import Callable

from textual.widgets import Static

#: Movement (in cells) below which a press counts as a click, not a drag.
CLICK_SLOP = 1


class Splitter(Static):
    """A 1-cell divider that can be dragged to resize an adjacent pane.

    ``vertical`` dividers sit between two side-by-side panes and report
    horizontal movement; ``horizontal`` dividers sit above a pane and report
    vertical movement.
    """

    DEFAULT_CSS = """
    Splitter {
        width: 1;
        height: 100%;
        background: $panel;
        content-align: center middle;
        color: $text-muted;
    }
    Splitter.-horizontal {
        width: 100%;
        height: 1;
        content-align: left middle;
        padding: 0 1;
    }
    Splitter:hover, Splitter.-dragging {
        background: $accent;
        color: $text;
    }
    """

    def __init__(
        self,
        orientation: str = "vertical",
        *,
        label: str = "",
        on_drag: Callable[[int], None] | None = None,
        on_commit: Callable[[], None] | None = None,
        on_click: Callable[[], None] | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(label, id=id, classes=classes)
        self.orientation = orientation
        self._on_drag = on_drag
        self._on_commit = on_commit
        self._on_click = on_click
        self._dragging = False
        self._last = 0
        self._moved = 0
        if orientation == "horizontal":
            self.add_class("-horizontal")

    @property
    def dragging(self) -> bool:
        return self._dragging

    def set_label(self, label: str) -> None:
        """Replace the divider's label (used by the log's collapse header)."""
        self.update(label)

    def _axis(self, event) -> int:
        return event.screen_x if self.orientation == "vertical" else event.screen_y

    def on_mouse_down(self, event) -> None:
        if event.button != 1:
            return
        event.stop()
        self._dragging = True
        self._moved = 0
        self._last = self._axis(event)
        self.set_class(True, "-dragging")
        self.capture_mouse()

    def on_mouse_move(self, event) -> None:
        if not self._dragging:
            return
        event.stop()
        current = self._axis(event)
        delta = current - self._last
        if not delta:
            return
        self._last = current
        self._moved += abs(delta)
        if self._on_drag:
            self._on_drag(delta)

    def on_mouse_up(self, event) -> None:
        if not self._dragging:
            return
        event.stop()
        self._dragging = False
        self.remove_class("-dragging")
        self.release_mouse()
        if self._moved < CLICK_SLOP and self._on_click is not None:
            # A press with no movement is a click, not a resize.
            self._on_click()
        elif self._on_commit:
            self._on_commit()
