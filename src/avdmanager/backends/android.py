"""Backend for the modern ``android`` CLI (cmdline-tools 23+).

``sdkmanager`` is deprecated in favour of this tool:

    WARNING: The SDK Manager CLI tool (sdkmanager) is deprecated.
    Android CLI will be used instead.

Where the two differ, the ``android`` equivalents are preferred; licence
acceptance has no ``android`` equivalent so it still shells out to
``sdkmanager --licenses`` when that binary is available.
"""

from __future__ import annotations

from collections.abc import Sequence

from avdmanager.config import ResolvedPaths


class AndroidCliBackend:
    """``android sdk ...`` / ``android emulator ...``"""

    name = "android-cli"
    label = "android CLI"
    modern = True

    # -- queries ---------------------------------------------------------- #

    def list_packages_argv(
        self, paths: ResolvedPaths, *, pattern: str | None = None, include_all: bool = True
    ) -> list[str]:
        argv = [paths.android_cli, "sdk", "list"]
        if include_all:
            argv.append("--all")
        if pattern:
            argv.append(pattern)
        return argv

    def installed_packages_argv(self, paths: ResolvedPaths) -> list[str]:
        # Without --all the new CLI reports installed packages only.
        return [paths.android_cli, "sdk", "list"]

    # -- mutations -------------------------------------------------------- #

    def install_argv(self, paths: ResolvedPaths, packages: Sequence[str]) -> list[str]:
        return [paths.android_cli, "sdk", "install", *packages]

    def remove_argv(self, paths: ResolvedPaths, packages: Sequence[str]) -> list[str]:
        return [paths.android_cli, "sdk", "remove", *packages]

    def update_all_argv(self, paths: ResolvedPaths) -> list[str]:
        return [paths.android_cli, "sdk", "update"]

    def accept_licenses_argv(self, paths: ResolvedPaths) -> list[str]:
        # `android sdk` has no licence subcommand; sdkmanager still does.
        return [paths.sdkmanager, "--licenses"]

    # -- emulators ------------------------------------------------------- #

    def start_emulator_argv(
        self, paths: ResolvedPaths, name: str, opts: Sequence[str]
    ) -> list[str]:
        return [paths.android_cli, "emulator", "start", name, *opts]

    def stop_emulator_argv(self, paths: ResolvedPaths, name: str) -> list[str]:
        return [paths.android_cli, "emulator", "stop", name]

    def emu_kill_argv(self, paths: ResolvedPaths) -> list[str]:
        return [paths.adb, "emu", "kill"]