"""AVD lifecycle operations, all driven through ``avdmanager``.

``avdmanager`` remains the tool for AVD CRUD on both the modern and legacy
installations -- the ``android emulator`` subcommands do not expose the same
detail (hardware profile ids, skins, sdcard sizes) that the AVD tree needs.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

from avdmanager import parse
from avdmanager.config import ResolvedPaths
from avdmanager.models import Avd, Device, Target
from avdmanager.runner import Job, JobRunner

#: AVD names accept only these characters (enforced by avdmanager itself and
#: mirrored by the reference extension's input validation).
NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class CreateRequest:
    """Everything ``avdmanager create avd`` needs."""

    name: str
    package_path: str
    device_id: int | None = None
    sdcard: str = ""
    avd_home: str = ""
    force: bool = True


def validate_avd_name(name: str, existing: set[str] | None = None) -> str | None:
    """Return an error message, or ``None`` when the name is acceptable."""
    if not name.strip():
        return "Name cannot be blank."
    if not NAME_RE.match(name):
        return f"{name!r} is invalid. Use letters, digits and underscores only."
    if existing and name in existing:
        return f"An AVD named {name!r} already exists."
    return None


class AvdService:
    """Read and mutate AVDs."""

    def __init__(self, runner: JobRunner, paths: ResolvedPaths) -> None:
        self._runner = runner
        self._paths = paths

    # -- queries ---------------------------------------------------------- #

    async def list_avds(self) -> list[Avd]:
        result = await self._runner.capture([self._paths.avdmanager, "list", "avd"], timeout=60)
        if not result.ok:
            return []
        # Parsing happens off the event loop so a large AVD set never stalls
        # the UI.
        return await asyncio.to_thread(parse.parse_avd_list, result.output)

    async def list_devices(self) -> list[Device]:
        result = await self._runner.capture([self._paths.avdmanager, "list", "device"], timeout=90)
        if not result.ok:
            return []
        return await asyncio.to_thread(parse.parse_device_list, result.output)

    async def list_targets(self) -> list[Target]:
        result = await self._runner.capture([self._paths.avdmanager, "list", "target"], timeout=90)
        if not result.ok:
            return []
        return await asyncio.to_thread(parse.parse_target_list, result.output)

    # -- mutations -------------------------------------------------------- #

    def create_argv(self, request: CreateRequest) -> list[str]:
        argv = [
            self._paths.avdmanager,
            "create",
            "avd",
            "-n",
            request.name,
            "-k",
            # avdmanager wants semicolons; listings use slashes.
            parse.to_avdmanager_package_arg(request.package_path),
        ]
        if request.device_id is not None and request.device_id >= 0:
            argv += ["--device", str(request.device_id)]
        if request.avd_home:
            argv += ["--path", f"{request.avd_home.rstrip('/')}/{request.name}.avd"]
        if request.sdcard:
            argv += ["-c", request.sdcard]
        if request.force:
            argv.append("--force")
        return argv

    async def create(self, request: CreateRequest, on_line=None) -> Job:
        # avdmanager asks "Do you wish to create a custom hardware profile?"
        # with [no] as the default, so answer "no" rather than "y".
        return await self._runner.start(
            self.create_argv(request),
            label=f"Create AVD {request.name}",
            mutation=True,
            answer_text="no",
            on_line=on_line,
        )

    def rename_argv(self, name: str, new_name: str) -> list[str]:
        return [self._paths.avdmanager, "move", "avd", "-n", name, "-r", new_name]

    async def rename(self, name: str, new_name: str, on_line=None) -> Job:
        return await self._runner.start(
            self.rename_argv(name, new_name),
            label=f"Rename {name} -> {new_name}",
            mutation=True,
            on_line=on_line,
        )

    def delete_argv(self, name: str) -> list[str]:
        return [self._paths.avdmanager, "delete", "avd", "-n", name]

    async def delete(self, name: str, on_line=None) -> Job:
        return await self._runner.start(
            self.delete_argv(name),
            label=f"Delete AVD {name}",
            mutation=True,
            on_line=on_line,
        )


# --------------------------------------------------------------------------- #
# Opening AVD files outside the editor
# --------------------------------------------------------------------------- #

#: Preferred desktop opener per platform.
_OPENERS = {
    "darwin": ["open"],
    "win32": ["explorer"],
    "linux": ["xdg-open"],
}


async def reveal_in_file_manager(path: str) -> tuple[bool, str]:
    """Open ``path`` in the OS file manager."""
    opener = _OPENERS.get(sys.platform)
    if opener is None:
        return False, f"No file manager opener for {sys.platform}"
    try:
        proc = await asyncio.create_subprocess_exec(
            *opener,
            path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return False, str(exc)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        return False, err.decode(errors="replace").strip()
    return True, f"Opened {path}"


def open_in_editor(path: str, editor: str = "") -> tuple[bool, str]:
    """Open a file with ``$EDITOR`` (returns immediately)."""
    command = editor or os.environ.get("EDITOR", "")
    if not command:
        return False, "Set $EDITOR or configure one in Settings."
    try:
        subprocess.Popen([*shlex.split(command), path])
    except OSError as exc:
        return False, str(exc)
    return True, f"Opened {path}"