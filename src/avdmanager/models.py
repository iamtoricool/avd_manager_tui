"""Domain models shared across the app.

These are plain dataclasses with no behaviour that touches I/O so that they can
be constructed freely inside parsers and compared easily in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# AVDs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Avd:
    """A single Android Virtual Device, as reported by ``avdmanager list avd``."""

    name: str
    based_on: str = ""
    device: str = ""
    path: str = ""
    target: str = ""
    tag_abi: str = ""
    sdcard: str = ""
    skin: str = ""

    @property
    def config_ini(self) -> str:
        """Absolute path of the AVD's ``config.ini``."""
        return f"{self.path}/config.ini" if self.path else ""

    @property
    def summary(self) -> str:
        """One-line description used in lists."""
        return " | ".join(p for p in (self.based_on, self.tag_abi) if p)

    def detail_rows(self) -> list[tuple[str, str]]:
        return [
            ("Name", self.name),
            ("Based on", self.based_on),
            ("Device", self.device),
            ("Target", self.target),
            ("Tag/ABI", self.tag_abi),
            ("Sdcard", self.sdcard),
            ("Skin", self.skin),
            ("Path", self.path),
            ("config.ini", self.config_ini),
        ]


@dataclass(frozen=True, slots=True)
class Device:
    """A hardware profile from ``avdmanager list device``.

    ``id`` is the numeric id accepted by ``avdmanager create avd --device``.
    """

    id: int
    id_name: str
    name: str
    oem: str = ""
    tag: str = ""

    @property
    def summary(self) -> str:
        parts = [self.oem, self.tag, f"ID: {self.id}"]
        return " | ".join(p for p in parts if p)


@dataclass(frozen=True, slots=True)
class Target:
    """An installed platform from ``avdmanager list target``."""

    id: int
    id_name: str
    name: str
    type: str = ""
    api_level: str = ""
    revision: str = ""

    @property
    def summary(self) -> str:
        return f"API {self.api_level} | rev {self.revision}" if self.revision else ""


# --------------------------------------------------------------------------- #
# SDK packages
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Package:
    """An SDK package row.

    ``path`` uses the canonical slash form (``build-tools/36.0.0``); the
    ``avdmanager create avd -k`` flag needs the semicolon form, which
    :func:`avdmanager.parse.to_avdmanager_package_arg` produces.
    """

    path: str
    version: str = ""
    description: str = ""
    installed: bool = False
    #: Number of additional versions available, parsed from a ``(+N)`` suffix.
    updates: int = 0

    @property
    def segments(self) -> list[str]:
        return self.path.split("/")

    @property
    def category(self) -> str:
        """Top-level grouping key, e.g. ``platforms`` or ``build-tools``."""
        return self.segments[0]

    @property
    def name(self) -> str:
        """Human-readable short name, e.g. ``build-tools/36.0.0`` -> ``36.0.0``."""
        return self.segments[-1] if self.segments else self.path

    @property
    def group(self) -> str:
        """Second-level grouping key used by the tree.

        For platforms and system images this collapses the API level so the tree
        reads ``Android 16 (Baklava)`` with one child per variant, matching the
        reference extension's behaviour. Everything else groups by category.
        """
        segs = self.segments
        cat = segs[0]
        if cat in ("platforms", "platform-tools") and len(segs) >= 2:
            return _android_label(segs[1])
        if cat == "system-images" and len(segs) >= 2:
            return _android_label(segs[1])
        if cat == "extras" and len(segs) >= 2:
            return f"extras-{segs[1]}"
        return cat

    @property
    def variant(self) -> str:
        """The sub-label shown next to the package inside a group."""
        segs = self.segments
        cat = segs[0]
        if cat == "system-images" and len(segs) >= 4:
            return f"{segs[2]} / {segs[3]}"
        if cat == "platforms" and len(segs) >= 3:
            return "/".join(segs[2:])
        if cat == "extras" and len(segs) >= 3:
            return "/".join(segs[2:])
        if cat in ("platforms", "platform-tools") and len(segs) == 2:
            return ""
        return self.name

    @property
    def summary(self) -> str:
        bits = [f"ver {self.version}"]
        if self.installed:
            bits.append("installed")
        if self.updates:
            bits.append(f"+{self.updates} update{'s' if self.updates != 1 else ''}")
        return " • ".join(bits)

    def detail_rows(self) -> list[tuple[str, str]]:
        rows = [
            ("Package", self.path),
            ("Version", self.version),
            ("Description", self.description),
            ("Status", "Installed" if self.installed else "Available"),
        ]
        if self.updates:
            rows.append(("Updates", f"{self.updates} newer version(s) available"))
        return rows


@dataclass(slots=True)
class PackageIndex:
    """Available + installed packages merged into one lookup structure.

    ``available`` comes from ``--list`` and ``installed`` from
    ``--list_installed``. A path present in both is marked installed and keeps
    the available version visible so upgrades can be detected.
    """

    available: list[Package] = field(default_factory=list)
    installed: list[Package] = field(default_factory=list)

    def merged(self) -> list[Package]:
        """All packages, de-duplicated by path, with ``installed`` resolved."""
        by_path: dict[str, Package] = {}
        for pkg in self.available:
            by_path[pkg.path] = pkg
        for pkg in self.installed:
            existing = by_path.get(pkg.path)
            by_path[pkg.path] = Package(
                path=pkg.path,
                version=existing.version if existing else pkg.version,
                description=pkg.description or (existing.description if existing else ""),
                installed=True,
            )
        return sorted(by_path.values(), key=lambda p: p.path)

    def installed_paths(self) -> set[str]:
        return {p.path for p in self.installed}

    def has_upgrade(self, pkg: Package) -> bool:
        """True when an installed package has a newer version available."""
        if not pkg.installed:
            return False
        avail = next((p for p in self.available if p.path == pkg.path), None)
        if avail is None or not avail.version:
            return False
        return avail.version != pkg.version


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _android_label(segment: str) -> str:
    """``android-36`` -> ``Android 36 (Baklava)`` using the bundled API table."""
    from avdmanager.parse import api_level_info

    raw = segment.removeprefix("android-")
    info = api_level_info(raw)
    if info is None:
        return f"Android {raw}"
    code = info["code"]
    version = info["version"]
    return f"{version} ({code})" if code else version
