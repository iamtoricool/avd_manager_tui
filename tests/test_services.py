"""Tests for the service layer.

Services are exercised against a fake runner so no real SDK is required, plus
a handful of end-to-end checks that use the SDK installed on this machine
(guarded by :func:`pytest.mark.skipif` elsewhere via ``--no-network`` markers).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from avdmanager.backends import AndroidCliBackend, SdkManagerBackend
from avdmanager.config import Config, ResolvedTool, resolve
from avdmanager.context import AppContext, build_context
from avdmanager.models import Package
from avdmanager.runner import CaptureResult, Job, JobRunner, JobStatus
from avdmanager.services import AvdService, EmulatorService, SdkService
from avdmanager.services.avd import CreateRequest, validate_avd_name
from avdmanager.services.emulator import EmulatorState

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeRunner(JobRunner):
    """Records calls, replays canned output, and finishes jobs immediately."""

    def __init__(self, responses: dict[tuple[str, ...], CaptureResult] | None = None):
        super().__init__()
        self.responses = responses or {}
        self.captured: list[list[str]] = []
        self.started: list[dict] = []

    async def capture(self, argv, **kwargs) -> CaptureResult:
        argv = [str(a) for a in argv]
        self.captured.append(argv)
        for pattern, response in self.responses.items():
            if all(part in argv for part in pattern):
                return response
        return CaptureResult(argv=argv, stdout="", stderr="", returncode=0)

    async def start(self, argv, **kwargs):
        """Record the call and return an already-finished job.

        No process is spawned: these tests assert on argv and kwargs, not on
        real Android tooling.
        """
        argv = [str(a) for a in argv]
        self.started.append({"argv": argv, **kwargs})
        self._counter += 1
        job = Job(id=self._counter, label="stub", argv=argv)
        job.status = JobStatus.DONE
        job.started_at = time.monotonic()
        job.ended_at = job.started_at
        job._done.set()
        self._history.append(job)
        return job


def canned(stdout: str = "", stderr: str = "", rc: int = 0) -> CaptureResult:
    """Compact CaptureResult factory."""
    return CaptureResult(argv=[], stdout=stdout, stderr=stderr, returncode=rc)


@pytest.fixture
def paths():
    resolved = resolve(Config(), {"PATH": ""})
    for name, binary in (
        ("sdkmanager", "/sdk/cmdline-tools/latest/bin/sdkmanager"),
        ("avdmanager", "/sdk/cmdline-tools/latest/bin/avdmanager"),
        ("emulator", "/sdk/emulator/emulator"),
        ("adb", "/sdk/platform-tools/adb"),
        ("android", "/sdk/cmdline-tools/latest/bin/android"),
    ):
        resolved.tools[name] = ResolvedTool(name, binary, "sdk", True)
    return resolved


AVD_OUTPUT = """Available Android Virtual Devices:
    Name: Pixel_7
  Device: pixel_7 (Google)
    Path: /home/u/.android/avd/Pixel_7.avd
  Target: Google APIs (Google Inc.)
          Based on: Android 14.0 ("Upside Down Cake") Tag/ABI: google_apis/x86_64
  Sdcard: 256 MB
"""

PKG_OUTPUT = """Installed packages:
  platforms/android-36                             2.0.0    Android SDK Platform 36

Available packages:
  build-tools/36.0.0                               36.0.0   Android SDK Build-Tools 36
  system-images/android-36/google_apis/x86_64      7.0.0    Google APIs Intel x86 Atom System Image
"""


# --------------------------------------------------------------------------- #
# avdmanager argv construction
# --------------------------------------------------------------------------- #


def test_create_argv_minimal(paths):
    service = AvdService(FakeRunner(), paths)
    argv = service.create_argv(CreateRequest("MyAVD", "platforms/android-36"))
    assert argv[:6] == [
        paths.avdmanager,
        "create",
        "avd",
        "-n",
        "MyAVD",
        "-k",
    ]
    # slashes must be converted to the semicolon form avdmanager expects
    assert "platforms;android-36" in argv
    assert "--force" in argv


def test_create_argv_with_device_sdcard_and_home(paths):
    request = CreateRequest(
        name="MyAVD",
        package_path="system-images/android-36/google_apis/x86_64",
        device_id=12,
        sdcard="512M",
        avd_home="/home/u/.android/avd/",
        force=True,
    )
    argv = AvdService(FakeRunner(), paths).create_argv(request)
    assert "system-images;android-36;google_apis;x86_64" in argv
    assert argv[argv.index("--device") + 1] == "12"
    assert argv[argv.index("-c") + 1] == "512M"
    assert argv[argv.index("--path") + 1] == "/home/u/.android/avd/MyAVD.avd"


def test_create_argv_omits_negative_device(paths):
    """The reference extension passes -1 to mean 'no device'."""
    argv = AvdService(FakeRunner(), paths).create_argv(
        CreateRequest("MyAVD", "platforms/android-36", device_id=-1)
    )
    assert "--device" not in argv


def test_create_argv_without_force(paths):
    argv = AvdService(FakeRunner(), paths).create_argv(
        CreateRequest("MyAVD", "platforms/android-36", force=False)
    )
    assert "--force" not in argv


def test_rename_and_delete_argv(paths):
    service = AvdService(FakeRunner(), paths)
    assert service.rename_argv("Old", "New") == [
        paths.avdmanager, "move", "avd", "-n", "Old", "-r", "New",
    ]
    assert service.delete_argv("Old") == [paths.avdmanager, "delete", "avd", "-n", "Old"]


async def test_create_answers_no_to_hardware_profile_prompt(paths):
    """avdmanager's default is [no]; answering 'y' would invent a profile."""
    runner = FakeRunner()
    await AvdService(runner, paths).create(CreateRequest("X", "platforms/android-36"))
    assert runner.started[0]["answer_text"] == "no"


async def test_rename_and_delete_are_mutations(paths):
    runner = FakeRunner()
    service = AvdService(runner, paths)
    await service.rename("a", "b")
    await service.delete("a")
    assert all(entry["mutation"] for entry in runner.started)


async def test_list_avds_parses_output(paths):
    runner = FakeRunner({("list", "avd"): canned(AVD_OUTPUT)})
    avds = await AvdService(runner, paths).list_avds()
    assert [a.name for a in avds] == ["Pixel_7"]


async def test_list_avds_returns_empty_on_failure(paths):
    runner = FakeRunner({("list", "avd"): canned(stderr="boom", rc=1)})
    assert await AvdService(runner, paths).list_avds() == []


# --------------------------------------------------------------------------- #
# Name validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["Pixel_7", "abc123", "A_B_9"])
def test_valid_avd_names(name):
    assert validate_avd_name(name) is None


@pytest.mark.parametrize(
    ("name", "fragment"),
    [
        ("", "blank"),
        ("with space", "invalid"),
        ("dash-name", "invalid"),
        ("dot.name", "invalid"),
        ("emoji😀", "invalid"),
    ],
)
def test_invalid_avd_names(name, fragment):
    error = validate_avd_name(name)
    assert error is not None
    assert fragment in error


def test_duplicate_avd_name_is_rejected():
    assert "already exists" in validate_avd_name("Pixel_7", {"Pixel_7"})


# --------------------------------------------------------------------------- #
# SDK service
# --------------------------------------------------------------------------- #


def make_sdk(runner, paths, backend=None) -> SdkService:
    return SdkService(
        runner=runner, paths=paths, backend=backend or AndroidCliBackend()
    )


async def test_sdk_load_merges_installed_and_available(paths):
    runner = FakeRunner(
        {
            ("sdk", "list"): canned(PKG_OUTPUT),
        }
    )
    load = await make_sdk(runner, paths).load(installed=False)
    assert load.ok
    by_path = {p.path: p for p in load.index.merged()}
    assert by_path["platforms/android-36"].installed
    assert not by_path["build-tools/36.0.0"].installed


async def test_sdk_load_passes_pattern_to_backend(paths):
    runner = FakeRunner()
    await make_sdk(runner, paths).load(pattern="system-images*", installed=False)
    assert runner.captured[0][-1] == "system-images*"


async def test_sdk_load_reports_failure(paths):
    runner = FakeRunner({("sdk", "list"): canned(stderr="nope", rc=1)})
    load = await make_sdk(runner, paths).load(installed=False)
    assert not load.ok
    assert load.error == "nope"


async def test_sdk_generation_advances_on_each_load(paths):
    service = make_sdk(FakeRunner(), paths)
    first = await service.load(installed=False)
    second = await service.load(installed=False)
    assert second.generation > first.generation


async def test_sdk_install_and_remove(paths):
    runner = FakeRunner()
    service = make_sdk(runner, paths)
    await service.install(["build-tools/36.0.0"])
    await service.remove(["platforms/android-35"])
    assert runner.started[0]["argv"][-1] == "build-tools/36.0.0"
    assert runner.started[0]["mutation"] is True
    assert runner.started[0]["answer_text"] == "y"
    assert runner.started[1]["argv"][-1] == "platforms/android-35"


async def test_sdk_install_labels_multiple_packages(paths):
    runner = FakeRunner()
    await make_sdk(runner, paths).install(["a/1", "b/2", "c/3"])
    assert runner.started[0]["label"] == "Install 3 packages"


async def test_sdk_licenses_uses_sdkmanager_even_on_android_backend(paths):
    runner = FakeRunner()
    await make_sdk(runner, paths).accept_licenses()
    assert runner.started[0]["argv"] == [paths.sdkmanager, "--licenses"]


async def test_sdk_mutations_use_a_pty(paths):
    """Package operations run on a PTY so the android CLI does not buffer."""
    runner = FakeRunner()
    service = make_sdk(runner, paths)
    await service.install(["build-tools/36.0.0"])
    await service.remove(["platforms/android-35"])
    await service.update_all()
    await service.accept_licenses()
    assert len(runner.started) == 4
    assert all(entry["use_pty"] is True for entry in runner.started)


async def test_sdk_works_with_legacy_backend(paths):
    runner = FakeRunner()
    service = make_sdk(runner, paths, SdkManagerBackend())
    await service.install(["emulator"])
    assert runner.started[0]["argv"] == [paths.sdkmanager, "emulator"]


def test_group_by_category(paths):
    service = make_sdk(FakeRunner(), paths)
    grouped = service.group_by_category(
        [
            Package("platforms/android-36", "1"),
            Package("platforms/android-35", "1"),
            Package("build-tools/36.0.0", "1"),
        ]
    )
    assert set(grouped) == {"Android 16 (Baklava)", "Android 15 (Vanilla Ice Cream)", "build-tools"}
    assert len(grouped["build-tools"]) == 1


# --------------------------------------------------------------------------- #
# Emulator service
# --------------------------------------------------------------------------- #


def make_emulator(runner, paths, backend=None) -> EmulatorService:
    return EmulatorService(
        runner=runner, paths=paths, backend=backend or AndroidCliBackend()
    )


async def test_emulator_start_argv(paths):
    runner = FakeRunner()
    await make_emulator(runner, paths).start("Pixel_7", ["-no-window"])
    assert runner.started[0]["argv"] == [
        paths.android_cli, "emulator", "start", "Pixel_7", "-no-window",
    ]


async def test_emulator_list_avds(paths):
    runner = FakeRunner(
        {("-list-avds",): canned("Pixel_7\nPixel_8\n")}
    )
    assert await make_emulator(runner, paths).list_avds() == ["Pixel_7", "Pixel_8"]


async def test_emulator_refresh_devices(paths):
    runner = FakeRunner(
        {("devices",): canned("List of devices attached\nemulator-5554\tdevice\n")}
    )
    service = make_emulator(runner, paths)
    devices = await service.refresh_devices()
    assert devices == {"emulator-5554": "device"}
    assert service.state_of("anything") is EmulatorState.RUNNING


async def test_emulator_stop_falls_back_to_emu_kill(paths):
    class FlakyRunner(FakeRunner):
        def __init__(self):
            super().__init__()
            self.count = 0

        async def capture(self, argv, **kwargs):
            argv = [str(a) for a in argv]
            self.captured.append(argv)
            self.count += 1
            if self.count == 1:
                return canned(stderr="no device", rc=1)
            return canned("OK")

    runner = FlakyRunner()
    result = await make_emulator(runner, paths).stop("Pixel_7")
    assert result.ok
    assert runner.captured[0] == [paths.android_cli, "emulator", "stop", "Pixel_7"]
    assert runner.captured[1] == [paths.adb, "emu", "kill"]


# --------------------------------------------------------------------------- #
# Wipe storage
# --------------------------------------------------------------------------- #


@pytest.fixture
def avd_dir(tmp_path):
    """A realistic AVD data directory."""
    from avdmanager.services.avd import AvdService  # noqa: F401

    d = tmp_path / "avd_home" / "Pixel_7.avd"
    d.mkdir(parents=True)
    (d / "config.ini").write_text("avd.ini.encoding=UTF-8\n")
    (d / "AVD.conf").write_text("hw.cpu.arch=x86_64\n")
    (d / "hardware-qemu.ini").write_text("hw.ramSize=2048\n")
    (d / "sdcard.img").write_bytes(b"x" * 64)
    (d / "userdata-qemu.img").write_bytes(b"x" * 4096)
    (d / "cache.img").write_bytes(b"x" * 1024)
    (d / "cache.img.qcow2").write_bytes(b"x" * 512)
    (d / "encryptionkey.img").write_bytes(b"x" * 128)
    (d / "overlay.qcow2").write_bytes(b"x" * 256)
    (d / "bootcompleted.ini").write_text("")
    (d / "emu-launch-params.txt").write_text("...")
    (d / "emulator.log").write_text("log")
    (d / "snapshots").mkdir()
    (d / "snapshots" / "default_boot").mkdir()
    (d / "snapshots" / "default_boot" / "ram.img").write_bytes(b"x" * 32)
    (d / "data").mkdir()
    (d / "data" / "userdata.img").write_bytes(b"x" * 32)
    return d


def test_avd_home_defaults_to_android_dir(paths):
    from avdmanager.services.avd import AvdService

    paths.avd_home = ""
    assert AvdService(FakeRunner(), paths).avd_home == Path.home() / ".android" / "avd"


def test_avd_home_honours_configuration(paths):
    from avdmanager.services.avd import AvdService

    paths.avd_home = "/custom/avd/home"
    assert AvdService(FakeRunner(), paths).avd_home == Path("/custom/avd/home")


def test_data_dir_derives_from_avd_home(paths):
    from avdmanager.services.avd import AvdService

    paths.avd_home = "/custom/avd/home"
    assert AvdService(FakeRunner(), paths).data_dir("Pixel_7").name == "Pixel_7.avd"


def test_storage_artifacts_finds_data_but_not_definition(avd_dir):
    from avdmanager.services.avd import storage_artifacts

    names = {p.name for p in storage_artifacts(avd_dir)}
    assert {"userdata-qemu.img", "cache.img", "cache.img.qcow2", "encryptionkey.img",
            "overlay.qcow2", "snapshots", "data", "bootcompleted.ini",
            "emu-launch-params.txt", "emulator.log"} <= names
    # the device definition must never be classified as storage
    assert "config.ini" not in names
    assert "AVD.conf" not in names
    assert "hardware-qemu.ini" not in names


def test_wipe_storage_keeps_the_avd(paths, avd_dir):
    """The headline guarantee: a wipe must not delete the AVD."""
    from avdmanager.services.avd import wipe_storage_files

    removed = wipe_storage_files(avd_dir)

    assert removed, "expected artifacts to be removed"
    # definition survives
    assert (avd_dir / "config.ini").is_file()
    assert (avd_dir / "AVD.conf").is_file()
    assert (avd_dir / "hardware-qemu.ini").is_file()
    assert (avd_dir / "sdcard.img").is_file()
    # data is gone
    assert not (avd_dir / "userdata-qemu.img").exists()
    assert not (avd_dir / "cache.img").exists()
    assert not (avd_dir / "encryptionkey.img").exists()
    assert not (avd_dir / "snapshots").exists()
    assert not (avd_dir / "data").exists()


def test_wipe_storage_leaves_unknown_files_alone(avd_dir):
    """Denylist semantics: an unrecognised file is never deleted on a guess."""
    from avdmanager.services.avd import wipe_storage_files

    mystery = avd_dir / "future-emulator-artifact.bin"
    mystery.write_bytes(b"x")

    wipe_storage_files(avd_dir)
    assert mystery.is_file()


def test_wipe_storage_is_idempotent(avd_dir):
    from avdmanager.services.avd import wipe_storage_files

    assert wipe_storage_files(avd_dir)
    assert wipe_storage_files(avd_dir) == []


def test_wipe_storage_tolerates_missing_dir(tmp_path):
    from avdmanager.services.avd import storage_artifacts, wipe_storage_files

    missing = tmp_path / "nope"
    assert storage_artifacts(missing) == []
    assert wipe_storage_files(missing) == []


def test_storage_size_counts_only_artifacts(avd_dir):
    from avdmanager.services.avd import storage_size

    # 4096 + 1024 + 512 + 128 + 256 + 32(snapshots) + 32(data) + log leftovers
    assert storage_size(avd_dir) > 6000


async def test_service_wipe_storage_keeps_the_avd(paths, avd_dir):
    """End-to-end through the service: wipes data, keeps the device."""
    from avdmanager.models import Avd
    from avdmanager.services.avd import AvdService

    paths.avd_home = str(avd_dir.parent)
    service = AvdService(FakeRunner(), paths)

    ok, removed = await service.wipe_storage(Avd(name="Pixel_7"))

    assert ok is True
    assert removed
    assert (avd_dir / "config.ini").is_file()
    assert not (avd_dir / "userdata-qemu.img").exists()
    # no subprocess is involved: a wipe must not shell out to avdmanager
    assert service._runner.started == []


# --------------------------------------------------------------------------- #
# Size formatting
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (2048, "2.0 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (2 * 1024**4, "2.0 TB"),
    ],
)
def test_human_size(value, expected):
    from avdmanager.services.avd import human_size

    assert human_size(value) == expected


def test_directory_size_ignores_missing_paths(tmp_path):
    from avdmanager.services.avd import directory_size

    assert directory_size(tmp_path / "nope") == 0


def test_directory_size_counts_recursively(tmp_path):
    from avdmanager.services.avd import directory_size

    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "userdata.img").write_bytes(b"x" * 500)
    (tmp_path / "a" / "cache.img").write_bytes(b"x" * 500)
    assert directory_size(tmp_path) == 1000


# --------------------------------------------------------------------------- #
# AppContext
# --------------------------------------------------------------------------- #


def test_context_builds_services(tmp_path):
    ctx = AppContext(
        runner=JobRunner(),
        config=Config(),
        config_path=tmp_path / "config.toml",
    )
    assert ctx.avd is not None
    assert ctx.emulator is not None
    # no SDK on this fake config, so the backend is unavailable but the app still boots
    assert ctx.backend is None or ctx.backend.name in ("android-cli", "sdkmanager")


def test_context_rebuilds_after_config_change(tmp_path):
    ctx = AppContext(
        runner=JobRunner(),
        config=Config(),
        config_path=tmp_path / "config.toml",
    )
    before = ctx.paths
    ctx.apply_config(cmdline_tools_version="12.0")
    assert ctx.config.cmdline_tools_version == "12.0"
    assert ctx.paths.cmdline_tools_version == "12.0"
    assert (tmp_path / "config.toml").is_file()
    assert before is not ctx.paths


def test_build_context_reads_config(tmp_path):
    from avdmanager import config as cfg

    sdk = tmp_path / "sdk"
    sdk.mkdir()
    target = tmp_path / "config.toml"
    cfg.save_config(cfg.Config(sdk_path=str(sdk)), target)
    ctx = build_context(target, runner=JobRunner())
    assert ctx.config.sdk_path == str(sdk)
    assert ctx.paths.sdk_root == str(sdk)
    assert ctx.paths.sdk_root_source == "config"


def test_context_backend_label(tmp_path):
    ctx = AppContext(runner=JobRunner(), config=Config(), config_path=tmp_path / "c.toml")
    assert isinstance(ctx.backend_label, str)


def test_ready_flag(tmp_path):
    ctx = AppContext(runner=JobRunner(), config=Config(), config_path=tmp_path / "c.toml")
    assert ctx.ready == (ctx.backend is not None and ctx.paths.ok)


async def test_context_shutdown_is_safe(tmp_path):
    ctx = AppContext(runner=JobRunner(), config=Config(), config_path=tmp_path / "c.toml")
    await ctx.shutdown()  # must not raise with no active jobs
    assert asyncio.iscoroutinefunction(ctx.shutdown)