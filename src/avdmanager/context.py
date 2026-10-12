"""Composition root.

Holds the resolved configuration, the chosen backend and the long-lived
services, and rebuilds them when settings change. The UI talks to this object
and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from avdmanager import config as config_mod
from avdmanager.backends import Backend, BackendUnavailable, detect_backend
from avdmanager.config import Config, ResolvedPaths
from avdmanager.runner import Job, JobRunner
from avdmanager.services import AvdService, EmulatorService, SdkService


class BackendUnavailableState(RuntimeError):
    """Raised when no SDK command-line tool is usable."""


@dataclass
class AppContext:
    """Everything the UI needs, rebuilt on demand."""

    runner: JobRunner
    config: Config
    config_path: Path | str
    paths: ResolvedPaths = field(init=False)
    backend: Backend | None = field(init=False, default=None)
    backend_error: str = field(init=False, default="")
    avd: AvdService = field(init=False, default=None)  # type: ignore[assignment]
    sdk: SdkService = field(init=False, default=None)  # type: ignore[assignment]
    emulator: EmulatorService = field(init=False, default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.rebuild()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def rebuild(self) -> None:
        """Re-resolve paths, pick a backend and recreate the services."""
        self.paths = config_mod.resolve(self.config)
        self.backend = None
        self.backend_error = ""
        try:
            self.backend = detect_backend(
                self.paths, prefer_android_cli=self.config.prefer_android_cli
            )
        except BackendUnavailable as exc:
            self.backend_error = str(exc)

        self.avd = AvdService(self.runner, self.paths)
        if self.backend is not None:
            self.sdk = SdkService(
                runner=self.runner, paths=self.paths, backend=self.backend
            )
            self.emulator = EmulatorService(
                runner=self.runner, paths=self.paths, backend=self.backend
            )
        else:
            self.sdk = None  # type: ignore[assignment]
            self.emulator = None  # type: ignore[assignment]

    def save_config(self) -> None:
        config_mod.save_config(self.config, self.config_path)

    def apply_config(self, **changes) -> None:
        """Update settings, persist them and rebuild the service graph."""
        for key, value in changes.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.save_config()
        self.rebuild()

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #

    @property
    def ready(self) -> bool:
        """True when the app can actually talk to the SDK."""
        return self.backend is not None and self.paths.ok

    @property
    def backend_label(self) -> str:
        return self.backend.label if self.backend else "unavailable"

    def track(self, job: Job) -> Job:
        return job

    async def shutdown(self) -> None:
        await self.runner.shutdown()


def build_context(
    config_path: Path | None = None, *, runner: JobRunner | None = None
) -> AppContext:
    """Create an :class:`AppContext` from on-disk configuration."""
    path = config_path or config_mod.default_config_path()
    return AppContext(
        runner=runner or JobRunner(),
        config=config_mod.load_config(path),
        config_path=path,
    )