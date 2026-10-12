"""Emulator and adb integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum

from avdmanager import parse
from avdmanager.backends import Backend
from avdmanager.config import ResolvedPaths
from avdmanager.runner import CaptureResult, Job, JobRunner


class EmulatorState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    UNKNOWN = "unknown"


@dataclass
class EmulatorService:
    """Launch, stop and monitor emulators."""

    runner: JobRunner
    paths: ResolvedPaths
    backend: Backend
    #: Serial -> state, for devices seen on adb.
    devices: dict[str, str] = field(default_factory=dict)
    #: AVD name -> the job that started it.
    _jobs: dict[str, Job] = field(default_factory=dict, repr=False)

    # -- queries ---------------------------------------------------------- #

    async def list_avds(self) -> list[str]:
        """AVD names known to the emulator binary."""
        if not self.paths.emulator:
            return []
        result = await self.runner.capture([self.paths.emulator, "-list-avds"], timeout=60)
        if not result.ok:
            return []
        return await asyncio.to_thread(parse.parse_avd_names, result.stdout)

    async def version(self) -> str:
        if not self.paths.emulator:
            return ""
        result = await self.runner.capture([self.paths.emulator, "-version"], timeout=30)
        return parse.parse_emulator_version(result.output) if result.ok else ""

    async def refresh_devices(self) -> dict[str, str]:
        """Poll ``adb devices`` and return the current mapping."""
        if not self.paths.adb:
            return self.devices
        result = await self.runner.capture([self.paths.adb, "devices"], timeout=30)
        if not result.ok:
            return self.devices
        pairs = await asyncio.to_thread(parse.parse_adb_devices, result.stdout)
        self.devices = dict(pairs)
        return self.devices

    def state_of(self, avd_name: str) -> EmulatorState:
        """Best-effort state for an AVD based on its job and adb serials."""
        job = self._jobs.get(avd_name)
        if job is not None and not job.status.terminal:
            return EmulatorState.RUNNING
        if any(serial.startswith("emulator-") for serial in self.devices):
            return EmulatorState.RUNNING
        if job is not None and job.status.value == "done":
            # Process exited: the emulator window closed.
            return EmulatorState.STOPPED
        return EmulatorState.STOPPED

    def running_avds(self) -> set[str]:
        return {name for name in self._jobs if self.state_of(name) is EmulatorState.RUNNING}

    # -- lifecycle -------------------------------------------------------- #

    async def start(self, avd_name: str, opts: list[str] | None = None, on_line=None) -> Job:
        argv = self.backend.start_emulator_argv(self.paths, avd_name, opts or [])
        job = await self.runner.start(
            argv,
            label=f"Emulator {avd_name}",
            on_line=on_line,
            timeout=None,
        )
        self._jobs[avd_name] = job
        return job

    async def stop(self, avd_name: str) -> CaptureResult:
        """Stop an emulator via the backend, falling back to ``adb emu kill``."""
        argv = self.backend.stop_emulator_argv(self.paths, avd_name)
        result = await self.runner.capture(argv, timeout=60)
        if not result.ok:
            result = await self.runner.capture(self.backend.emu_kill_argv(self.paths), timeout=30)
        await self.refresh_devices()
        return result

    async def stop_job(self, avd_name: str) -> bool:
        """Kill the emulator process this app started."""
        job = self._jobs.get(avd_name)
        if job is None or job.status.terminal:
            return False
        await self.runner.cancel(job)
        return True