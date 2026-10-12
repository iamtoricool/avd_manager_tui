"""SDK package operations: list, install, remove, update, licences."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from avdmanager import parse
from avdmanager.backends import Backend
from avdmanager.config import ResolvedPaths
from avdmanager.models import Package, PackageIndex
from avdmanager.runner import Job, JobRunner


@dataclass(slots=True)
class PackageLoad:
    """Result of a package query, tagged so stale responses can be discarded."""

    generation: int
    index: PackageIndex
    pattern: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class SdkService:
    """Wraps the selected backend behind a stable interface."""

    runner: JobRunner
    paths: ResolvedPaths
    backend: Backend
    _generation: int = field(default=0, init=False)

    # -- queries ---------------------------------------------------------- #

    @property
    def generation(self) -> int:
        return self._generation

    def bump_generation(self) -> int:
        """Invalidate any in-flight load so late results are discarded."""
        self._generation += 1
        return self._generation

    async def load(
        self, *, pattern: str | None = None, installed: bool = True
    ) -> PackageLoad:
        """Fetch and parse the package table.

        ``installed`` additionally queries the installed-only listing, which is
        how legacy ``sdkmanager`` reports packages that are present but no
        longer offered in the repository.
        """
        generation = self.bump_generation()

        available_argv = self.backend.list_packages_argv(self.paths, pattern=pattern)
        available = await self.runner.capture(available_argv, timeout=300)
        if not available.ok:
            return PackageLoad(
                generation=generation,
                index=PackageIndex(),
                pattern=pattern or "",
                error=available.output.strip() or "Could not list SDK packages.",
            )

        available_table = await asyncio.to_thread(parse.parse_package_table, available.output)
        index = PackageIndex(
            available=available_table.all(),
            installed=[],
        )

        # Legacy output only reports installed packages under the installed
        # heading; the new CLI lists them in both, so this is a no-op there.
        if installed:
            installed_argv = self.backend.installed_packages_argv(self.paths)
            result = await self.runner.capture(installed_argv, timeout=300)
            if result.ok:
                index.installed = await asyncio.to_thread(
                    parse.parse_installed_packages, result.output
                )

        return PackageLoad(generation=generation, index=index, pattern=pattern or "")

    # -- mutations -------------------------------------------------------- #

    async def install(self, packages: list[str], on_line=None) -> Job:
        argv = self.backend.install_argv(self.paths, packages)
        return await self.runner.start(
            argv,
            label=_summarise("Install", packages),
            mutation=True,
            answer_text="y",
            on_line=on_line,
        )

    async def remove(self, packages: list[str], on_line=None) -> Job:
        argv = self.backend.remove_argv(self.paths, packages)
        return await self.runner.start(
            argv,
            label=_summarise("Remove", packages),
            mutation=True,
            answer_text="y",
            on_line=on_line,
        )

    async def update_all(self, on_line=None) -> Job:
        return await self.runner.start(
            self.backend.update_all_argv(self.paths),
            label="Update all packages",
            mutation=True,
            answer_text="y",
            on_line=on_line,
        )

    async def accept_licenses(self, on_line=None) -> Job:
        return await self.runner.start(
            self.backend.accept_licenses_argv(self.paths),
            label="Accept SDK licenses",
            mutation=True,
            # sdkmanager asks "Accept? (y/N):" once per pending license.
            answer_text="y",
            on_line=on_line,
        )

    # -- helpers ---------------------------------------------------------- #

    @staticmethod
    def group_by_category(packages: list[Package]) -> dict[str, list[Package]]:
        """Group packages by their tree group, preserving order."""
        grouped: dict[str, list[Package]] = {}
        for pkg in packages:
            grouped.setdefault(pkg.group, []).append(pkg)
        return grouped


def _summarise(action: str, packages: list[str]) -> str:
    if len(packages) == 1:
        return f"{action} {packages[0]}"
    return f"{action} {len(packages)} packages"