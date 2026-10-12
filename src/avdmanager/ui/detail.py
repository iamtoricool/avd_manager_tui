"""Detail pane for the selected tree node."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


class DetailPane(VerticalScroll):
    """Renders key/value detail for whatever is selected in the sidebar."""

    DEFAULT_CSS = """
    DetailPane { height: 1fr; padding: 1 2; overflow-y: auto; }
    DetailPane > #detail-title { text-style: bold; color: $accent;
                                padding-bottom: 1; }
    DetailPane > .detail-empty { color: $text-muted; padding: 2 0; }
    DetailPane > .detail-row { height: auto; padding-left: 2; }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="detail-title")
        yield Static("", id="detail-body")

    # ------------------------------------------------------------------ #

    def show_empty(self, message: str = "Nothing selected.") -> None:
        self.query_one("#detail-title", Static).update("")
        self.query_one("#detail-body", Static).update(
            f'[class="detail-empty"]{message}[/]'
        )

    def show_error(self, message: str) -> None:
        self.query_one("#detail-title", Static).update("Error")
        self.query_one("#detail-body", Static).update(
            f'[class="detail-empty health-error"]{message}[/]'
        )

    def show_rows(self, title: str, rows: list[tuple[str, str]], *, footer: str = "") -> None:
        """Render a title plus aligned key/value rows."""
        self.query_one("#detail-title", Static).update(title)
        lines = [_render_row(key, value) for key, value in rows if value]
        if footer:
            lines.append("")
            lines.append(f'[class="detail-empty"]{footer}[/]')
        self.query_one("#detail-body", Static).update("\n".join(lines) or "")

    def show_markdown(self, title: str, body: str) -> None:
        self.query_one("#detail-title", Static).update(title)
        self.query_one("#detail-body", Static).update(body)


def _render_row(key: str, value: str) -> str:
    path_class = "detail-value detail-path" if looks_like_path(value) else "detail-value"
    return (
        f'[class="detail-row"][class="detail-key"]{_escape(key)}:[/] '
        f'[class="{path_class}"]{_escape(value)}[/]'
    )


def looks_like_path(value: str) -> bool:
    return value.startswith(("/", "~", "$")) or "://" in value


def _escape(text: str) -> str:
    """Escape Rich markup in values coming from the SDK."""
    return text.replace("[", "\\[")