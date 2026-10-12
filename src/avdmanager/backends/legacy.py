"""Backend for the legacy ``sdkmanager`` binary.

Kept for SDK installations whose cmdline-tools predate the ``android`` CLI.
Command shapes mirror ``src/cmd/SDKManager.ts`` from the reference extension.
"""

from __future__ import annotations

from collections.abc import Sequence

from avdmanager.config import ResolvedPaths


class SdkManagerBackend:
    """``sdkmanager --list`` / ``<package>`` / ``--uninstall`` / ..."""

    name = "sdkmanager"
    label = "sdkmanager (legacy)"
    modern = False

    # -- queries ---------------------------------------------------------- #

    def list_packages_argv(
        self, paths: ResolvedPaths, *, pattern: str | None = None, include_all: bool = True
    ) -> list[str]:
        argv = [paths.sdkmanager, "--list"]
        if not include_all:
            argv.append("--installed")
        return argv

    def installed_packages_argv(self, paths: ResolvedPaths) -> list[str]:
        return [paths.sdkmanager, "--list_installed"]

    # -- mutations -------------------------------------------------------- #

    def install_argv(self, paths: ResolvedPaths, packages: Sequence[str]) -> list[str]:
        # sdkmanager takes the package as a positional argument.
        return [paths.sdkmanager, *packages]

    def remove_argv(self, paths: ResolvedPaths, packages: Sequence[str]) -> list[str]:
        return [paths.sdkmanager, "--uninstall", *packages]

    def update_all_argv(self, paths: ResolvedPaths) -> list[str]:
        return [paths.sdkmanager, "--update", "--channel=0"]

    def accept_licenses_argv(self, paths: ResolvedPaths) -> list[str]:
        return [paths.sdkmanager, "--licenses"]

    # -- emulators ------------------------------------------------------- #

    def start_emulator_argv(
        self, paths: ResolvedPaths, name: str, opts: Sequence[str]
    ) -> list[str]:
        # The legacy tool takes `@<name>` rather than a positional argument.
        return [paths.emulator, f"@{name}", *opts]

    def stop_emulator_argv(self, paths: ResolvedPaths, name: str) -> list[str]:
        # `adb emu kill` targets whichever emulator is attached.
        return [paths.adb, "emu", "kill"]

    def emu_kill_argv(self, paths: ResolvedPaths) -> list[str]:
        return [paths.adb, "emu", "kill"]