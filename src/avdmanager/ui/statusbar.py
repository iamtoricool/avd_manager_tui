"""Status bar: backend, SDK health, active jobs."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from avdmanager.runner import Job, JobRunner


class StatusBar(Horizontal):
    """One-line summary of SDK state and running jobs."""

    DEFAULT_CSS = """
    StatusBar { height: 1; padding: 0 1; background: $panel-darken-1; }
    StatusBar > Static { width: auto; padding: 0 1 0 0; color: $text-muted; }
    StatusBar > #status-jobs.-busy { color: $accent; text-style: bold; }
    StatusBar > #status-health.-error { color: $error; }
    StatusBar > #status-health.-warn { color: $warning; }
    StatusBar > #status-health.-ok { color: $success; }
    """

    def __init__(self, runner: JobRunner, **kwargs) -> None:
        super().__init__(**kwargs)
        self._runner = runner
        self._message = ""
        self._message_until = 0.0
        self._health = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="status-backend")
        yield Static("", id="status-health")
        yield Static("", id="status-jobs")

    def on_mount(self) -> None:
        self.set_interval(0.5, self.tick)

    # ------------------------------------------------------------------ #

    def notify(self, message: str, seconds: float = 3.0) -> None:
        """Show a transient message in the status area."""
        self._message = message
        self._message_until = time.monotonic() + seconds

    def tick(self) -> None:
        self._update_jobs()
        if self._message and time.monotonic() > self._message_until:
            self._message = ""

    def _update_health(self) -> None:
        """Refresh the health cell (issues are pushed by the app)."""
        widget = self.query_optional("#status-health", Static)
        if widget is None:
            return
        self._health = self._health or ""
        widget.update(self._health)

    def set_backend(self, label: str) -> None:
        try:
            self.query_one("#status-backend", Static).update(f" backend: {label}")
        except Exception:  # pragma: no cover
            pass

    def set_health(self, issues: list[str]) -> None:
        self._health = " SDK: ok" if not issues else f" SDK: {len(issues)} issue(s)"
        try:
            widget = self.query_one("#status-health", Static)
        except Exception:  # pragma: no cover
            return
        widget.update(self._health)
        widget.set_class(not issues, "-ok")
        widget.set_class(bool(issues), "-error")

    def _update_jobs(self) -> None:
        try:
            widget = self.query_one("#status-jobs", Static)
        except Exception:  # pragma: no cover
            return

        if self._message:
            widget.set_class(False, "-busy")
            widget.update(f" {self._message}")
            return

        active = self._runner.active_jobs
        if not active:
            widget.set_class(False, "-busy")
            widget.update("")
            return

        label = ", ".join(job.label for job in active[:2])
        extra = f" (+{len(active) - 2})" if len(active) > 2 else ""
        widget.set_class(True, "-busy")
        widget.update(f" ▶ {label}{extra}  [{_elapsed(active[0])}]  ctrl+c cancels")

    def cancel_focused_job(self) -> Job | None:
        """Cancel the most recently started active job."""
        active = self._runner.active_jobs
        return active[-1] if active else None


def _elapsed(job: Job) -> str:
    seconds = int(job.duration)
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"