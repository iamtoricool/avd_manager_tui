"""Backend abstraction over the Android SDK command-line tools.

Two implementations exist:

* :mod:`avdmanager.backends.android` -- the modern ``android`` CLI shipped with
  cmdline-tools 23+, preferred by default
* :mod:`avdmanager.backends.legacy` -- the older ``sdkmanager`` binary, used as
  a fallback

Both expose the same operations so the services above them do not care which is
active. Backends only *build argv lists* and parse output; orchestration and
process supervision stay in the runner and services.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from avdmanager.config import ResolvedPaths
from avdmanager.models import Package


@runtime_checkable
class Backend(Protocol):
    """Operations the app needs from an SDK command-line tool."""

    #: Stable identifier, e.g. ``"android-cli"``.
    name: str
    #: Human readable name for the status bar.
    label: str
    #: ``True`` when this backend is the tool vendor recommends.
    modern: bool

    # -- queries ---------------------------------------------------------- #

    def list_packages_argv(
        self, paths: ResolvedPaths, *, pattern: str | None = None, include_all: bool = True
    ) -> list[str]:
        """argv for listing packages."""

    def installed_packages_argv(self, paths: ResolvedPaths) -> list[str]:
        """argv for listing only installed packages."""

    # -- mutations -------------------------------------------------------- #

    def install_argv(self, paths: ResolvedPaths, packages: Sequence[str]) -> list[str]:
        """argv to download/install the given package paths."""

    def remove_argv(self, paths: ResolvedPaths, packages: Sequence[str]) -> list[str]:
        """argv to uninstall the given package paths."""

    def update_all_argv(self, paths: ResolvedPaths) -> list[str]:
        """argv to update every installed package."""

    def accept_licenses_argv(self, paths: ResolvedPaths) -> list[str]:
        """argv to accept all pending SDK licences."""

    # -- emulators ------------------------------------------------------- #

    def start_emulator_argv(
        self, paths: ResolvedPaths, name: str, opts: Sequence[str]
    ) -> list[str]:
        """argv to launch an AVD."""

    def stop_emulator_argv(self, paths: ResolvedPaths, name: str) -> list[str]:
        """argv to stop a running AVD."""

    def emu_kill_argv(self, paths: ResolvedPaths) -> list[str]:
        """argv for the ``adb emu kill`` escape hatch."""


#: Categories shown in the SDK Platforms tree, mirroring the reference extension.
PLATFORM_CATEGORIES = ("platforms", "system-images", "sources")

#: Everything else lands in the SDK Tools tree.
TOOL_CATEGORIES_EXCLUDED = PLATFORM_CATEGORIES


def is_platform_package(pkg: Package) -> bool:
    return pkg.category in PLATFORM_CATEGORIES


def is_tool_package(pkg: Package) -> bool:
    return pkg.category not in PLATFORM_CATEGORIES