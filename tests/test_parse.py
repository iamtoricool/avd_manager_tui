"""Tests for the pure CLI output parsers.

The fixtures under ``tests/fixtures`` were captured verbatim from a real
Android SDK (cmdline-tools 23.0.0) so these tests guard against regressions in
the messiest part of the application.
"""

from __future__ import annotations

import pytest

from avdmanager import parse


# --------------------------------------------------------------------------- #
# Output normalisation
# --------------------------------------------------------------------------- #


def test_clean_output_keeps_final_carriage_return_redraw():
    """avdmanager repaints a progress bar over the same logical line."""
    raw = "Loading...  \r[==] 50% Loading...  \rAvailable Android targets:"
    assert parse.clean_output(raw) == "Available Android targets:"


def test_clean_output_keeps_unprogressed_lines():
    raw = "id: 1 or \"android-34\"\n     Name: Android API 34"
    assert parse.clean_output(raw) == raw


def test_clean_output_normalises_crlf():
    assert parse.clean_output("a\r\nb\r\n") == "a\nb\n"


# --------------------------------------------------------------------------- #
# AVDs
# --------------------------------------------------------------------------- #


def test_parse_avd_list_from_real_output(avd_list_output):
    avds = parse.parse_avd_list(avd_list_output)

    assert len(avds) == 1
    avd = avds[0]
    assert avd.name == "Pixel_36_Ultra"
    assert avd.device == "pixel (Google)"
    assert avd.path == "/home/elliot/.android/avd/Pixel_36_Ultra.avd"
    assert avd.target == "Google Play (Google Inc.)"
    # "Based on" and "Tag/ABI" share a continuation line and must be separated.
    assert avd.based_on == 'Android 16.0 ("Baklava")'
    assert avd.tag_abi == "google_apis_playstore/x86_64"
    assert avd.sdcard == "512 MB"


def test_parse_avd_list_handles_multiple_records():
    raw = """Available Android Virtual Devices:
    Name: Pixel_7_API_34
  Device: pixel_7 (Google)
    Path: /home/u/.android/avd/Pixel_7_API_34.avd
  Target: Google APIs (Google Inc.)
          Based on: Android 14.0 ("Upside Down Cake") Tag/ABI: google_apis/x86_64
  Sdcard: 256 MB
    Name: Tablet_API_33
  Device: tablet (Generic)
    Path: /home/u/.android/avd/Tablet_API_33.avd
  Target: Android 13.0 ("Tiramisu")
          Based on: Android 13.0 ("Tiramisu") Tag/ABI: default/x86_64
  Skin: 2048x2732
  Sdcard: 64 MB
"""
    avds = parse.parse_avd_list(raw)
    assert [a.name for a in avds] == ["Pixel_7_API_34", "Tablet_API_33"]
    assert avds[1].skin == "2048x2732"
    assert avds[1].sdcard == "64 MB"


def test_parse_avd_list_empty():
    assert parse.parse_avd_list("Available Android Virtual Devices:\n") == []


def test_avd_config_ini_and_summary(avd_list_output):
    avd = parse.parse_avd_list(avd_list_output)[0]
    assert avd.config_ini == "/home/elliot/.android/avd/Pixel_36_Ultra.avd/config.ini"
    assert avd.summary == 'Android 16.0 ("Baklava") | google_apis_playstore/x86_64'


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #


def test_parse_device_list_from_real_output(device_list_output):
    devices = parse.parse_device_list(device_list_output)

    # A current SDK ships ~96 hardware profiles.
    assert len(devices) > 90
    first = devices[0]
    assert first.id == 0
    assert first.id_name == "ai_glasses_displayless"
    assert first.name == "Audio Glasses"
    assert first.oem == "Google"
    assert first.tag == "ai-glasses"


def test_parse_device_list_handles_missing_tag():
    raw = """Available devices definitions:
id: 5 or "custom_phone"
    Name: Custom Phone
    OEM : Acme
---------
"""
    devices = parse.parse_device_list(raw)
    assert len(devices) == 1
    assert devices[0].tag == ""


def test_parse_device_list_ids_are_unique(device_list_output):
    devices = parse.parse_device_list(device_list_output)
    ids = [d.id for d in devices]
    assert len(ids) == len(set(ids))


def test_parse_device_list_preserves_quotes_in_names(device_list_output):
    names = {d.name for d in parse.parse_device_list(device_list_output)}
    assert '10.1" WXGA (Tablet)' in names


# --------------------------------------------------------------------------- #
# Targets
# --------------------------------------------------------------------------- #


def test_parse_target_list_strips_progress_bar(target_list_output):
    targets = parse.parse_target_list(target_list_output)

    assert targets, "progress bar prefix must not defeat parsing"
    t = targets[0]
    assert t.id == 1
    assert t.id_name == "android-34"
    assert t.type == "Platform"
    assert t.api_level == "34"
    assert t.revision == "3"


# --------------------------------------------------------------------------- #
# Packages
# --------------------------------------------------------------------------- #


def test_parse_package_table_from_real_output(package_list_output):
    table = parse.parse_package_table(package_list_output)

    assert set(table.sections) == {"installed", "available"}
    assert len(table.section("installed")) == 13
    assert len(table.section("available")) == 636


def test_parse_package_table_parses_every_row(package_list_output):
    """Regression guard for the pipe-delimiter bug in the reference extension."""
    assert "|" not in package_list_output

    table = parse.parse_package_table(package_list_output)
    for pkg in table.all():
        assert pkg.path, pkg
        assert pkg.version, pkg
        assert pkg.description, pkg
        assert not pkg.path.startswith("/"), pkg

    # Single-segment packages such as `emulator` must survive too.
    paths = {p.path for p in table.all()}
    assert {"emulator", "platform-tools"} <= paths


def test_parse_package_table_marks_installed_flag(package_list_output):
    table = parse.parse_package_table(package_list_output)
    assert all(p.installed for p in table.section("installed"))
    assert not any(p.installed for p in table.section("available"))


def test_parse_package_table_specific_rows(package_list_output):
    table = parse.parse_package_table(package_list_output)
    by_path = {p.path: p for p in table.all()}

    bt = by_path["build-tools/36.0.0"]
    assert bt.version == "36.0.0"
    assert bt.description == "Android SDK Build-Tools 36"
    assert bt.installed

    img = by_path["system-images/android-36/google_apis_playstore/x86_64"]
    assert img.installed
    assert img.description == "Google Play Intel x86_64 Atom System Image"

    available = by_path["add-ons/addon-google_apis-google-19"]
    assert available.version == "20.0.0"
    assert not available.installed


def test_parse_installed_packages(installed_list_output):
    installed = parse.parse_installed_packages(installed_list_output)
    assert len(installed) == 13
    assert all(p.installed for p in installed)
    assert {p.path for p in installed} >= {"emulator", "platform-tools", "cmake/3.22.1"}


def test_android_sdk_list_output_parses_identically(android_cli_list_output):
    """The new `android sdk list` reuses the same column layout."""
    table = parse.parse_package_table(android_cli_list_output)
    assert len(table.section("installed")) == 13


def test_parse_package_table_handles_two_column_row():
    raw = "Installed packages:\n  emulator    37.2.12\n"
    pkgs = parse.parse_installed_packages(raw)
    assert len(pkgs) == 1
    assert pkgs[0].path == "emulator"
    assert pkgs[0].description == ""


def test_parse_package_table_keeps_double_spaces_in_description():
    raw = 'Available packages:\n  foo/1.0    1.0    Some   spaced   text\n'
    pkgs = parse.parse_available_packages(raw)
    assert pkgs[0].description == "Some   spaced   text"


def test_parse_package_table_ignores_unindented_noise():
    raw = """WARNING: the SDK Manager CLI tool is deprecated.
Installed packages:
  emulator    37.2.12    Android Emulator
"""
    pkgs = parse.parse_installed_packages(raw)
    assert [p.path for p in pkgs] == ["emulator"]


def test_to_avdmanager_package_arg():
    assert (
        parse.to_avdmanager_package_arg("system-images/android-36/google_apis_playstore/x86_64")
        == "system-images;android-36;google_apis_playstore;x86_64"
    )


# --------------------------------------------------------------------------- #
# Package grouping helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "group", "variant"),
    [
        ("platforms/android-36", "Android 16 (Baklava)", ""),
        ("platforms/android-34", "Android 14 (Upside Down Cake)", ""),
        ("platforms;android-34", "Android 14 (Upside Down Cake)", ""),
        ("system-images/android-36/google_apis_playstore/x86_64",
         "Android 16 (Baklava)", "google_apis_playstore / x86_64"),
        ("build-tools/36.0.0", "build-tools", "36.0.0"),
        ("extras;google;m8", "extras-google", "m8"),
        ("extras/google/m8", "extras-google", "m8"),
        ("cmdline-tools/latest", "cmdline-tools", "latest"),
        ("emulator", "emulator", "emulator"),
    ],
)
def test_package_grouping(path, group, variant):
    pkg = parse.parse_available_packages(
        "Available packages:\n  " + path.replace(";", "/") + "    1.0    d\n"
    )[0]
    assert pkg.group == group
    assert pkg.variant == variant


def test_package_detail_rows(package_list_output):
    table = parse.parse_package_table(package_list_output)
    pkg = next(p for p in table.all() if p.path == "emulator")
    rows = dict(pkg.detail_rows())
    assert rows["Package"] == "emulator"
    assert rows["Status"] == "Installed"


# --------------------------------------------------------------------------- #
# PackageIndex merging
# --------------------------------------------------------------------------- #


def test_package_index_marks_installed_and_keeps_available_version():
    from avdmanager.models import Package, PackageIndex

    index = PackageIndex(
        available=[
            Package("build-tools/36.0.0", "36.1.0", "Build Tools", False),
            Package("emulator", "37.2.12", "Android Emulator", False),
        ],
        installed=[Package("build-tools/36.0.0", "36.0.0", "Build Tools", True)],
    )

    merged = {p.path: p for p in index.merged()}
    assert merged["build-tools/36.0.0"].installed
    # the available (newer) version wins so upgrades are detectable
    assert merged["build-tools/36.0.0"].version == "36.1.0"
    assert not merged["emulator"].installed

    # has_upgrade() is called with the *installed* package.
    installed = next(p for p in index.installed if p.path == "build-tools/36.0.0")
    assert index.has_upgrade(installed)
    assert not index.has_upgrade(Package("emulator", "37.2.12", "", True))


# --------------------------------------------------------------------------- #
# Emulator / adb
# --------------------------------------------------------------------------- #


def test_parse_emulator_version():
    raw = "Android emulator version 37.2.12.0 (build_id 16428233) (CL:N/A)\nCopyright..."
    assert parse.parse_emulator_version(raw) == "37.2.12.0"


def test_parse_avd_names():
    assert parse.parse_avd_names("Pixel_36_Ultra\n\nPixel_7\n") == [
        "Pixel_36_Ultra",
        "Pixel_7",
    ]


def test_parse_adb_devices():
    raw = """List of devices attached
emulator-5554          device
192.168.1.5:5555       offline
R58M123               unauthorized
"""
    assert parse.parse_adb_devices(raw) == [
        ("emulator-5554", "device"),
        ("192.168.1.5:5555", "offline"),
        ("R58M123", "unauthorized"),
    ]


# --------------------------------------------------------------------------- #
# API levels
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("36", "Android 16"),
        ("35", "Android 15"),
        ("34", "Android 14"),
        ("21", "Android 5.0"),
    ],
)
def test_api_level_info_known(level, expected):
    assert parse.api_level_info(level)["version"] == expected


def test_api_level_info_unknown_returns_none():
    assert parse.api_level_info("9999") is None