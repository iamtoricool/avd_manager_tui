"""Tests for backend argv construction and selection."""

from __future__ import annotations

import pytest

from avdmanager.backends import (
    AndroidCliBackend,
    BackendUnavailable,
    SdkManagerBackend,
    detect_backend,
)
from avdmanager.backends.base import Backend, is_platform_package, is_tool_package
from avdmanager.config import Config, resolve
from avdmanager.models import Package


@pytest.fixture
def paths():
    """A ResolvedPaths with every tool present, regardless of the real machine."""
    from avdmanager.config import ResolvedTool

    resolved = resolve(Config(sdk_path="/nonexistent"), {"PATH": ""})
    for name, binary in (
        ("sdkmanager", "/sdk/cmdline-tools/latest/bin/sdkmanager"),
        ("avdmanager", "/sdk/cmdline-tools/latest/bin/avdmanager"),
        ("emulator", "/sdk/emulator/emulator"),
        ("adb", "/sdk/platform-tools/adb"),
        ("android", "/sdk/cmdline-tools/latest/bin/android"),
    ):
        resolved.tools[name] = ResolvedTool(name, binary, "sdk", True)
    return resolved


@pytest.fixture
def bare_paths():
    from avdmanager.config import ResolvedTool

    resolved = resolve(Config(), {"PATH": ""})
    for name in ("sdkmanager", "avdmanager", "emulator", "adb", "android"):
        resolved.tools[name] = ResolvedTool(name, "", "missing", False)
    return resolved


# --------------------------------------------------------------------------- #
# android CLI backend
# --------------------------------------------------------------------------- #


def test_android_backend_metadata():
    backend = AndroidCliBackend()
    assert backend.name == "android-cli"
    assert backend.modern is True
    assert isinstance(backend, Backend)


def test_android_list_packages(paths):
    argv = AndroidCliBackend().list_packages_argv(paths)
    assert argv == [paths.android_cli, "sdk", "list", "--all"]


def test_android_list_packages_with_pattern(paths):
    argv = AndroidCliBackend().list_packages_argv(paths, pattern="system-images*")
    assert argv[-1] == "system-images*"


def test_android_list_packages_without_all(paths):
    argv = AndroidCliBackend().list_packages_argv(paths, include_all=False)
    assert "--all" not in argv


def test_android_installed_packages(paths):
    argv = AndroidCliBackend().installed_packages_argv(paths)
    assert argv == [paths.android_cli, "sdk", "list"]


def test_android_install(paths):
    argv = AndroidCliBackend().install_argv(paths, ["build-tools/36.0.0", "emulator"])
    assert argv == [paths.android_cli, "sdk", "install", "build-tools/36.0.0", "emulator"]


def test_android_remove(paths):
    argv = AndroidCliBackend().remove_argv(paths, ["platforms/android-35"])
    assert argv == [paths.android_cli, "sdk", "remove", "platforms/android-35"]


def test_android_update_all(paths):
    assert AndroidCliBackend().update_all_argv(paths) == [paths.android_cli, "sdk", "update"]


def test_android_licenses_falls_back_to_sdkmanager(paths):
    """`android sdk` has no licence subcommand."""
    argv = AndroidCliBackend().accept_licenses_argv(paths)
    assert argv == [paths.sdkmanager, "--licenses"]


def test_android_start_emulator(paths):
    argv = AndroidCliBackend().start_emulator_argv(paths, "Pixel_7", ["-no-window"])
    assert argv == [paths.android_cli, "emulator", "start", "Pixel_7", "-no-window"]


def test_android_stop_emulator(paths):
    assert AndroidCliBackend().stop_emulator_argv(paths, "Pixel_7") == [
        paths.android_cli,
        "emulator",
        "stop",
        "Pixel_7",
    ]


# --------------------------------------------------------------------------- #
# legacy backend
# --------------------------------------------------------------------------- #


def test_legacy_metadata():
    backend = SdkManagerBackend()
    assert backend.name == "sdkmanager"
    assert backend.modern is False


def test_legacy_list_packages(paths):
    assert SdkManagerBackend().list_packages_argv(paths) == [paths.sdkmanager, "--list"]


def test_legacy_install_uses_positional_argument(paths):
    """`sdkmanager <package>` -- not `sdkmanager install <package>`."""
    argv = SdkManagerBackend().install_argv(paths, ["emulator"])
    assert argv == [paths.sdkmanager, "emulator"]


def test_legacy_remove(paths):
    assert SdkManagerBackend().remove_argv(paths, ["emulator"]) == [
        paths.sdkmanager,
        "--uninstall",
        "emulator",
    ]


def test_legacy_update_all(paths):
    assert SdkManagerBackend().update_all_argv(paths) == [
        paths.sdkmanager,
        "--update",
        "--channel=0",
    ]


def test_legacy_licenses(paths):
    assert SdkManagerBackend().accept_licenses_argv(paths) == [paths.sdkmanager, "--licenses"]


def test_legacy_start_emulator_uses_at_prefix(paths):
    """The legacy emulator takes `@<name>`, unlike `android emulator start`."""
    argv = SdkManagerBackend().start_emulator_argv(paths, "Pixel_7", ["-no-audio"])
    assert argv == [paths.emulator, "@Pixel_7", "-no-audio"]


def test_legacy_stop_emulator(paths):
    assert SdkManagerBackend().stop_emulator_argv(paths, "Pixel_7") == [paths.adb, "emu", "kill"]


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def test_detect_prefers_android_cli(paths):
    assert detect_backend(paths).name == "android-cli"


def test_detect_honours_opt_out(paths):
    assert detect_backend(paths, prefer_android_cli=False).name == "sdkmanager"


def test_detect_falls_back_when_android_cli_missing(bare_paths):
    from avdmanager.config import ResolvedTool

    bare_paths.tools["sdkmanager"] = ResolvedTool(
        "sdkmanager", "/sdk/sdkmanager", "path", True
    )
    assert detect_backend(bare_paths).name == "sdkmanager"


def test_detect_falls_back_when_opted_in_but_unavailable(bare_paths):
    from avdmanager.config import ResolvedTool

    bare_paths.tools["sdkmanager"] = ResolvedTool("sdkmanager", "/sdk/smgr", "path", True)
    bare_paths.tools["android"] = ResolvedTool("android", "", "missing", False)
    assert detect_backend(bare_paths).name == "sdkmanager"


def test_detect_uses_android_cli_when_it_is_the_only_one(bare_paths):
    from avdmanager.config import ResolvedTool

    bare_paths.tools["android"] = ResolvedTool("android", "/sdk/android", "path", True)
    assert detect_backend(bare_paths).name == "android-cli"


def test_detect_raises_when_nothing_available(bare_paths):
    with pytest.raises(BackendUnavailable, match=r"[Cc]ommand-line Tools"):
        detect_backend(bare_paths)


# --------------------------------------------------------------------------- #
# Category helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    ["platforms/android-36", "system-images/android-36/google_apis/x86_64", "sources/android-36"],
)
def test_platform_categories(path):
    assert is_platform_package(Package(path))


@pytest.mark.parametrize(
    "path",
    ["build-tools/36.0.0", "emulator", "platform-tools", "cmdline-tools/latest", "ndk/28.2.1"],
)
def test_tool_categories(path):
    assert is_tool_package(Package(path))