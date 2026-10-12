"""Pure parsers for Android SDK command-line output.

Everything in this module is a pure function: no filesystem, no subprocess, no
global state. That keeps the messy parts of CLI output isolated and fully unit
tested against fixtures captured from a real SDK.

Portability note: the reference VS Code extension (``vscode-avdmanager``)
splits package rows on ``|``. Modern ``sdkmanager`` / ``android sdk list``
emit space-padded columns instead and contain no ``|`` at all, so that parser
returns nothing. We parse the real column format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from avdmanager.models import Avd, Device, Package, Target

# --------------------------------------------------------------------------- #
# Output normalisation
# --------------------------------------------------------------------------- #

# avdmanager paints an in-place progress bar ("Loading local repository...[==] 25%")
# using carriage returns, which ends up glued to the header line of the output.
_CARRIAGE_SPLIT = "\r"


def clean_output(text: str) -> str:
    """Normalise raw CLI output.

    * collapses ``\\r\\n`` to ``\\n``
    * keeps only the final redraw of any carriage-return progress line
    * strips trailing whitespace from each line
    """
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        segments = [s for s in raw.split(_CARRIAGE_SPLIT) if s.strip()]
        lines.append((segments[-1] if segments else "").rstrip())
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# AVDs
# --------------------------------------------------------------------------- #

# "Based on: ... Tag/ABI: ..." arrive on one continuation line; split them so each
# line holds exactly one key. Mirrors the fix in AVDTreeView's reference parser.
_TAB_ABI_SPLIT = re.compile(r"(?=\bTag/ABI\s*:)")

_KEY_VALUE = re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z /]*?)\s*:\s*(?P<value>.*)$")

_AVD_FIELDS = {
    "name": "name",
    "device": "device",
    "path": "path",
    "target": "target",
    "based on": "based_on",
    "tag/abi": "tag_abi",
    "sdcard": "sdcard",
    "skin": "skin",
}


def parse_avd_list(text: str) -> list[Avd]:
    """Parse ``avdmanager list avd`` output."""
    text = _TAB_ABI_SPLIT.sub("\n", clean_output(text))
    avds: list[Avd] = []
    current: dict[str, str] = {}

    for line in text.split("\n"):
        match = _KEY_VALUE.match(line)
        if not match:
            continue
        field_name = _AVD_FIELDS.get(match.group("key").strip().lower())
        if field_name is None:
            continue
        value = match.group("value").strip()
        if field_name == "name":
            if current.get("name"):
                avds.append(_build_avd(current))
            current = {}
        current[field_name] = value

    if current.get("name"):
        avds.append(_build_avd(current))
    return avds


def _build_avd(data: dict[str, str]) -> Avd:
    return Avd(
        name=data.get("name", ""),
        based_on=data.get("based_on", ""),
        device=data.get("device", ""),
        path=data.get("path", ""),
        target=data.get("target", ""),
        tag_abi=data.get("tag_abi", ""),
        sdcard=data.get("sdcard", ""),
        skin=data.get("skin", ""),
    )


# --------------------------------------------------------------------------- #
# Devices and targets
# --------------------------------------------------------------------------- #

_ID_LINE = re.compile(r'^\s*id\s*:\s*(?P<id>-?\d+)\s+or\s+"(?P<id_name>[^"]*)"')
_SEPARATOR = re.compile(r"^-+$")


def parse_device_list(text: str) -> list[Device]:
    """Parse ``avdmanager list device`` output (~400 entries in practice)."""
    return [
        Device(
            id=int(data.get("id", -1)),
            id_name=data.get("id_name", ""),
            name=data.get("name", ""),
            oem=data.get("oem", ""),
            tag=data.get("tag", ""),
        )
        for data in _iter_id_blocks(text, ("name", "oem", "tag"))
    ]


def parse_target_list(text: str) -> list[Target]:
    """Parse ``avdmanager list target`` output."""
    return [
        Target(
            id=int(data.get("id", -1)),
            id_name=data.get("id_name", ""),
            name=data.get("name", ""),
            type=data.get("type", ""),
            api_level=data.get("api level", ""),
            revision=data.get("revision", ""),
        )
        for data in _iter_id_blocks(text, ("name", "type", "api level", "revision"))
    ]


def _iter_id_blocks(text: str, keys: tuple[str, ...]) -> list[dict[str, str]]:
    """Walk ``id: N or "name"`` blocks followed by indented key/value lines."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in clean_output(text).split("\n"):
        if _SEPARATOR.match(line.strip()):
            continue
        id_match = _ID_LINE.match(line)
        if id_match:
            current = {"id": id_match.group("id"), "id_name": id_match.group("id_name")}
            blocks.append(current)
            continue
        if current is None:
            continue
        kv = _KEY_VALUE.match(line)
        if kv:
            key = kv.group("key").strip().lower()
            if key in keys:
                current[key] = kv.group("value").strip()
    return blocks


# --------------------------------------------------------------------------- #
# SDK packages
# --------------------------------------------------------------------------- #

_SECTION_HEADERS = {
    "installed packages": "installed",
    "available packages": "available",
    "available updates": "updates",
    "obsolete packages": "obsolete",
}

_SECTION_RE = re.compile(
    r"^\s*(?P<name>" + "|".join(_SECTION_HEADERS) + r")\s*:\s*$", re.IGNORECASE
)

# Columns are separated by runs of two or more spaces. ``maxsplit=2`` keeps any
# multi-space sequence inside the description column intact.
_COLUMN_SPLIT = re.compile(r"\s{2,}")

# The repository marks packages with newer versions as "30.0.0 (+2)".
_UPDATES_RE = re.compile(r"\s*\(\+(?P<count>\d+)\)\s*$")


@dataclass(slots=True)
class PackageTable:
    """Packages grouped by the section header they appeared under."""

    sections: dict[str, list[Package]] = field(default_factory=dict)
    other: list[Package] = field(default_factory=list)

    def all(self) -> list[Package]:
        """Every package, de-duplicated by path.

        Real ``--list`` output repeats installed packages inside the
        "Available packages" section too, so an installed copy always wins.
        """
        by_path: dict[str, Package] = {}
        for pkg in [*self.other, *(p for pkgs in self.sections.values() for p in pkgs)]:
            existing = by_path.get(pkg.path)
            by_path[pkg.path] = pkg if pkg.installed or existing is None else existing
        return sorted(by_path.values(), key=lambda p: p.path)

    def section(self, name: str) -> list[Package]:
        return self.sections.get(name, [])


def parse_package_table(text: str) -> PackageTable:
    """Parse ``sdkmanager --list`` / ``android sdk list`` output.

    Rows look like::

          build-tools/36.0.0        36.0.0   Android SDK Build-Tools 36
          emulator                  37.2.12  Android Emulator
    """
    table = PackageTable()
    section: str | None = None

    for line in clean_output(text).split("\n"):
        if not line.strip():
            continue

        header = _SECTION_RE.match(line)
        if header:
            section = _SECTION_HEADERS[header.group("name").lower()]
            table.sections.setdefault(section, [])
            continue

        if not line.startswith((" ", "\t")):
            # A non-indented, non-section line is noise (e.g. deprecation
            # warnings mixed into stdout by older tooling).
            continue

        parts = _COLUMN_SPLIT.split(line.strip(), maxsplit=2)
        if len(parts) < 2:
            continue

        version = parts[1].strip()
        updates_match = _UPDATES_RE.search(version)
        updates = 0
        if updates_match:
            updates = int(updates_match.group("count"))
            version = version[: updates_match.start()].strip()

        pkg = Package(
            path=parts[0].strip(),
            version=version,
            description=parts[2].strip() if len(parts) > 2 else "",
            installed=section == "installed",
            updates=updates,
        )
        if section is None:
            table.other.append(pkg)
        else:
            table.sections[section].append(pkg)

    return table


def parse_installed_packages(text: str) -> list[Package]:
    """Convenience wrapper returning just the installed rows."""
    return parse_package_table(text).section("installed")


def parse_available_packages(text: str) -> list[Package]:
    """Convenience wrapper returning just the not-yet-installed rows."""
    return parse_package_table(text).section("available")


def to_avdmanager_package_arg(package_path: str) -> str:
    """Convert a slash package path to the form ``avdmanager create avd -k`` wants.

    ``system-images/android-36/google_apis_playstore/x86_64`` becomes
    ``system-images;android-36;google_apis_playstore;x86_64``.
    """
    return ";".join(package_path.split("/"))


# --------------------------------------------------------------------------- #
# Emulator and adb
# --------------------------------------------------------------------------- #

_EMULATOR_VERSION = re.compile(r"Android emulator version\s+(?P<version>[\d.]+)")


def parse_emulator_version(text: str) -> str:
    match = _EMULATOR_VERSION.search(text)
    return match.group("version") if match else ""


def parse_avd_names(text: str) -> list[str]:
    """Parse ``emulator -list-avds`` / ``android emulator list`` output."""
    return [line.strip() for line in clean_output(text).split("\n") if line.strip()]


_ADB_DEVICE_ROW = re.compile(
    r"^(?P<serial>\S+)\s+(?P<status>device|offline|unauthorized|bootloader)\b"
)


def parse_adb_devices(text: str) -> list[tuple[str, str]]:
    """Parse ``adb devices`` into ``(serial, status)`` pairs."""
    devices: list[tuple[str, str]] = []
    for line in clean_output(text).split("\n"):
        match = _ADB_DEVICE_ROW.match(line.strip())
        if match:
            devices.append((match.group("serial"), match.group("status")))
    return devices


# --------------------------------------------------------------------------- #
# Android API level -> release name
# --------------------------------------------------------------------------- #

# Data adapted from the reference extension's src/lang/api_level.json (MIT),
# extended with API 35/36.
_API_LEVELS: tuple[dict[str, str], ...] = (
    {"level": "36", "code": "Baklava", "version": "Android 16"},
    {"level": "35", "code": "Vanilla Ice Cream", "version": "Android 15"},
    {"level": "34", "code": "Upside Down Cake", "version": "Android 14"},
    {"level": "33-ext5", "code": "Tiramisu", "version": "Android 13 (Tiramisu)"},
    {"level": "33-ext4", "code": "Tiramisu", "version": "Android 13 (Tiramisu)"},
    {"level": "33", "code": "Tiramisu", "version": "Android 13"},
    {"level": "32", "code": "S_V2", "version": "Android 12L"},
    {"level": "31", "code": "S", "version": "Android 12"},
    {"level": "30", "code": "R", "version": "Android 11"},
    {"level": "29", "code": "Q", "version": "Android 10"},
    {"level": "28", "code": "Pie", "version": "Android 9"},
    {"level": "27", "code": "Oreo", "version": "Android 8.1.0"},
    {"level": "26", "code": "Oreo", "version": "Android 8.0.0"},
    {"level": "25", "code": "Nougat", "version": "Android 7.1"},
    {"level": "24", "code": "Nougat", "version": "Android 7.0"},
    {"level": "23", "code": "Marshmallow", "version": "Android 6.0"},
    {"level": "22", "code": "Lollipop", "version": "Android 5.1"},
    {"level": "21", "code": "Lollipop", "version": "Android 5.0"},
    {"level": "20", "code": "KitKat Wear", "version": "Android 4.4W"},
    {"level": "19", "code": "KitKat", "version": "Android 4.4"},
    {"level": "18", "code": "Jelly Bean", "version": "Android 4.3.x"},
    {"level": "17", "code": "Jelly Bean", "version": "Android 4.2.x"},
    {"level": "16", "code": "Jelly Bean", "version": "Android 4.1.x"},
    {"level": "15", "code": "Ice Cream Sandwich", "version": "Android 4.0.3"},
    {"level": "14", "code": "Ice Cream Sandwich", "version": "Android 4.0.1"},
    {"level": "13", "code": "Honeycomb", "version": "Android 3.2.x"},
    {"level": "12", "code": "Honeycomb", "version": "Android 3.1"},
    {"level": "11", "code": "Honeycomb", "version": "Android 3.0"},
    {"level": "10", "code": "Gingerbread", "version": "Android 2.3.3"},
    {"level": "9", "code": "Gingerbread", "version": "Android 2.3"},
    {"level": "8", "code": "Froyo", "version": "Android 2.2.x"},
    {"level": "7", "code": "Eclair", "version": "Android 2.1"},
    {"level": "6", "code": "Eclair", "version": "Android 2.0.1"},
    {"level": "5", "code": "Eclair", "version": "Android 2.0"},
    {"level": "4", "code": "Donut", "version": "Android 1.6"},
    {"level": "3", "code": "Cupcake", "version": "Android 1.5"},
    {"level": "2", "code": "", "version": "Android 1.1"},
    {"level": "1", "code": "", "version": "Android 1.0"},
)


def api_level_info(level: str) -> dict[str, str] | None:
    """Look up ``{"level", "code", "version"}`` for an API level string."""
    return next((info for info in _API_LEVELS if info["level"] == level), None)