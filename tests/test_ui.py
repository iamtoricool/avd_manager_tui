"""End-to-end UI tests.

Textual's test harness drives the real app against a stub SDK so the layout,
bindings, dialogs and tree population are exercised without needing an Android
installation.
"""

from __future__ import annotations

import pytest
from textual.widgets import Input, Tree

from avdmanager.backends import AndroidCliBackend
from avdmanager.config import Config, ResolvedTool, resolve
from avdmanager.models import Avd, Package
from avdmanager.runner import JobRunner
from avdmanager.services.avd import AvdService
from avdmanager.ui.app import AvdManagerApp
from avdmanager.ui.dialogs.base import ConfirmModal
from avdmanager.ui.logpane import LogPane
from avdmanager.ui.sidebar import AVAILABLE_ICON, INSTALLED_ICON, Sidebar

AVD_TEXT = """Available Android Virtual Devices:
    Name: Pixel_36_Ultra
  Device: pixel (Google)
    Path: /home/u/.android/avd/Pixel_36_Ultra.avd
  Target: Google Play (Google Inc.)
          Based on: Android 16.0 ("Baklava") Tag/ABI: google_apis_playstore/x86_64
  Sdcard: 512 MB
    Name: Tablet_API_33
  Device: tablet (Generic)
    Path: /home/u/.android/avd/Tablet_API_33.avd
  Target: Android 13.0 ("Tiramisu")
          Based on: Android 13.0 ("Tiramisu") Tag/ABI: default/x86_64
  Sdcard: 64 MB
"""

PKG_TEXT = """Installed packages:
  build-tools/36.0.0                               36.0.0   Android SDK Build-Tools 36
  platforms/android-36                             2.0.0    Android SDK Platform 36
  system-images/android-36/google_apis_playstore/x86_64  7.0.0  Google Play Intel x86_64

Available packages:
  build-tools/35.0.0                               35.0.0   Android SDK Build-Tools 35
  platforms/android-34                             3.0.0    Android SDK Platform 34
  cmdline-tools/latest                             23.0.0   Android SDK Command-line Tools
  emulator                                         37.2.12  Android Emulator
  ndk/28.2.13676358                                28.2.1   NDK (Side by side)
"""

DEVICE_TEXT = """Available devices definitions:
id: 0 or "pixel_7"
    Name: Pixel 7
    OEM : Google
    Tag : phone
---------
id: 1 or "tablet"
    Name: 10.1" Tablet
    OEM : Generic
---------
"""


class StubRunner(JobRunner):
    """Answers every SDK query from canned fixtures."""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[dict] = []
        self.cancelled: list[object] = []

    async def capture(self, argv, **kwargs):
        from avdmanager.runner import CaptureResult

        argv = [str(a) for a in argv]
        joined = " ".join(argv)
        if "list avd" in joined:
            return CaptureResult(argv=argv, stdout=AVD_TEXT, stderr="", returncode=0)
        if "list device" in joined:
            return CaptureResult(argv=argv, stdout=DEVICE_TEXT, stderr="", returncode=0)
        if "sdk list" in joined:
            return CaptureResult(argv=argv, stdout=PKG_TEXT, stderr="", returncode=0)
        if "-list-avds" in joined:
            return CaptureResult(argv=argv, stdout="Pixel_36_Ultra\n", stderr="", returncode=0)
        if "devices" in joined:
            return CaptureResult(argv=argv, stdout="List of devices attached\n",
                                 stderr="", returncode=0)
        return CaptureResult(argv=argv, stdout="", stderr="", returncode=0)

    async def start(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.started.append({"argv": argv, **kwargs})
        job = await super().start([*argv], **kwargs)
        return job


def _stub_paths():
    """A ResolvedPaths that points at fake binaries regardless of this machine."""

    resolved = resolve(Config(), {"PATH": ""})
    for name, binary in (
        ("sdkmanager", "/sdk/cmdline-tools/latest/bin/sdkmanager"),
        ("avdmanager", "/sdk/cmdline-tools/latest/bin/avdmanager"),
        ("emulator", "/sdk/emulator/emulator"),
        ("adb", "/sdk/platform-tools/adb"),
        ("android", "/sdk/cmdline-tools/latest/bin/android"),
    ):
        resolved.tools[name] = ResolvedTool(name, binary, "sdk", True)
    resolved.issues = []
    return resolved


@pytest.fixture
def stub_context():
    from avdmanager.context import AppContext
    from avdmanager.services import EmulatorService, SdkService

    runner = StubRunner()
    paths = _stub_paths()
    ctx = AppContext(runner=runner, config=Config(), config_path="/tmp/avdmanager-test.toml")
    ctx.paths = paths
    ctx.backend = ctx.backend or AndroidCliBackend()
    ctx.avd = AvdService(runner, paths)
    ctx.sdk = SdkService(runner=runner, paths=paths, backend=ctx.backend)
    ctx.emulator = EmulatorService(runner=runner, paths=paths, backend=ctx.backend)
    return ctx


@pytest.fixture
async def app(stub_context):
    instance = AvdManagerApp(stub_context)
    async with instance.run_test() as pilot:
        await pilot.pause()
        # let the background loaders settle
        for _ in range(6):
            await pilot.pause(0.05)
        yield instance, pilot


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #


async def test_app_renders_with_all_regions(app):
    instance, _pilot = app
    assert instance.query_one("#sidebar", Sidebar)
    assert instance.query_one("#detail")
    assert instance.query_one("#logpane", LogPane)
    assert instance.query_one("#statusbar")


async def test_three_trees_are_present(app):
    instance, _pilot = app
    sidebar = instance.query_one("#sidebar", Sidebar)
    assert len(sidebar.trees) == 3
    for tree in sidebar.trees:
        assert isinstance(tree, Tree)


async def test_log_toggles(app):
    instance, pilot = app
    log = instance.query_one("#logpane", LogPane)
    assert not log.has_class("-hidden")
    await pilot.press("F4")
    assert log.has_class("-hidden")
    await pilot.press("F4")
    assert not log.has_class("-hidden")


async def test_status_bar_shows_backend(app):
    from textual.widgets import Static

    instance, _pilot = app
    status = instance.statusbar
    status.set_backend("android CLI")
    status.set_health([])
    assert "android CLI" in str(status.query_one("#status-backend", Static).content)
    assert "ok" in str(status.query_one("#status-health", Static).content)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


async def test_avds_are_loaded_into_tree(app):
    instance, _pilot = app
    tree = instance.sidebar.avd_tree
    labels = [str(node.label) for node in tree.root.children]
    assert any("Pixel_36_Ultra" in label for label in labels)
    assert any("Tablet_API_33" in label for label in labels)


async def test_packages_split_between_platforms_and_tools(app):
    instance, _pilot = app
    platforms = {str(n.label) for n in instance.sidebar.platforms_tree.root.children}
    tools = {str(n.label) for n in instance.sidebar.tools_tree.root.children}

    assert any("Android 16" in label for label in platforms)
    assert any("Android 14" in label for label in platforms)
    assert any("build-tools" in label for label in tools)
    assert any("emulator" in label for label in tools)
    assert any("ndk" in label for label in tools)
    # platforms must not leak into the tools tree
    assert not any("Android 16" in label for label in tools)


async def test_installed_and_available_are_distinguished(app):
    instance, _pilot = app
    labels = [str(n.label) for n in instance.sidebar.tools_tree.root.children]
    build_tools = next(label for label in labels if "build-tools" in label)
    # the group shows an installed/total count when anything is installed
    assert "1/2" in build_tools


async def test_refresh_reloads(app):
    instance, pilot = app
    instance.action_refresh_all()
    for _ in range(6):
        await pilot.pause(0.05)
    assert instance.sidebar.avd_tree.root.children


# --------------------------------------------------------------------------- #
# Selection and detail
# --------------------------------------------------------------------------- #


async def test_selecting_avd_shows_detail(app):
    instance, pilot = app
    tree = instance.sidebar.avd_tree
    tree.focus()
    await pilot.press("down")
    await pilot.pause()
    from textual.widgets import Static

    body = str(instance.query_one("#detail-body", Static).content)
    assert "Pixel_36_Ultra" in body
    assert "config.ini" in body


async def test_selecting_package_shows_detail(app):
    instance, pilot = app
    tree = instance.sidebar.tools_tree
    tree.focus()
    tree.root.children[0].expand()
    for _ in range(4):
        await pilot.pause(0.05)
    await pilot.press("down")
    await pilot.pause()
    from textual.widgets import Static

    body = str(instance.query_one("#detail-body", Static).content)
    assert "Package" in body or "build-tools" in body


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


async def test_filter_is_debounced(app):
    instance, pilot = app
    await pilot.press("f")
    for _ in range(3):
        await pilot.press("t")
        await pilot.pause(0.02)
    await pilot.pause()
    await pilot.pause(0.5)
    for _ in range(4):
        await pilot.pause(0.05)
    assert instance._pending_filter == "ttt"


async def test_filter_box_hidden_by_default(app):
    instance, _pilot = app
    assert instance.filter_input.display is False
    instance.action_focus_filter()
    assert instance.filter_input.display is True


# --------------------------------------------------------------------------- #
# Dialogs
# --------------------------------------------------------------------------- #


async def test_confirm_modal_renders_detail_lines(app):
    from textual.widgets import Static

    instance, pilot = app
    modal = ConfirmModal(
        "Delete AVD Pixel?",
        "This cannot be undone.",
        detail=["Path: /home/u/.android/avd/Pixel.avd"],
    )
    instance.push_screen(modal)
    await pilot.pause()
    labels = [str(w.content) for w in modal.query(Static)]
    assert any("Pixel" in label for label in labels)
    assert any("/home/u/.android/avd/Pixel.avd" in label for label in labels)


async def test_uninstall_shows_confirm_with_package_path(app):
    instance, pilot = app
    tree = instance.sidebar.tools_tree
    tree.focus()
    tree.root.children[0].expand()
    for _ in range(4):
        await pilot.pause(0.05)
    # move onto the first package leaf
    for _ in range(2):
        await pilot.press("down")
        await pilot.pause()
    pkg = instance.sidebar.selected_package()
    assert pkg is not None and pkg.installed, instance.sidebar.selected()

    await pilot.press("u")
    await pilot.pause()
    assert isinstance(instance.screen, ConfirmModal)
    detail = instance.screen._detail
    assert any(pkg.path in line for line in detail)


# --------------------------------------------------------------------------- #
# Batch marking
# --------------------------------------------------------------------------- #


async def test_space_marks_a_package(app):
    from avdmanager.ui.sidebar import MARK_ICON

    instance, pilot = app
    tree = instance.sidebar.tools_tree
    tree.focus()
    tree.root.children[0].expand()
    for _ in range(4):
        await pilot.pause(0.05)
    for _ in range(2):
        await pilot.press("down")
        await pilot.pause()

    await pilot.press("space")
    await pilot.pause()
    assert instance.sidebar.mark_count() == 1
    marked = instance.sidebar.marked_packages()
    assert len(marked) == 1
    assert MARK_ICON in str(tree.cursor_node.label)

    await pilot.press("space")
    await pilot.pause()
    assert instance.sidebar.mark_count() == 0
    assert instance.sidebar.marked_packages() == []


async def test_marking_multiple_packages(app):
    instance, pilot = app
    tree = instance.sidebar.tools_tree
    tree.focus()
    tree.root.children[0].expand()
    tree.root.children[1].expand()
    for _ in range(4):
        await pilot.pause(0.05)

    for _ in range(4):
        await pilot.press("down")
        await pilot.press("space")
        await pilot.pause()

    assert instance.sidebar.mark_count() >= 2
    assert len(instance.sidebar.marked_packages()) == instance.sidebar.mark_count()


async def test_escape_clears_marks(app):
    instance, pilot = app
    tree = instance.sidebar.tools_tree
    tree.focus()
    tree.root.children[0].expand()
    for _ in range(4):
        await pilot.pause(0.05)
    await pilot.press("down")
    await pilot.press("down")
    await pilot.press("space")
    await pilot.pause()
    assert instance.sidebar.mark_count() == 1

    instance.action_clear_marks()
    await pilot.pause()
    assert instance.sidebar.mark_count() == 0


async def test_batch_targets_fall_back_to_selection(app):
    instance, pilot = app
    tree = instance.sidebar.tools_tree
    tree.focus()
    tree.root.children[0].expand()
    for _ in range(4):
        await pilot.pause(0.05)
    for _ in range(2):
        await pilot.press("down")
        await pilot.pause()

    # with nothing marked, the highlighted node is the target
    assert len(instance._batch_targets(require_installed=True)) == 1


async def test_running_indicator_updates_avd_labels(app):
    from avdmanager.ui.sidebar import RUNNING_ICON

    instance, _pilot = app
    instance.sidebar.set_running({"Pixel_36_Ultra"})
    node = instance.sidebar.avd_tree.root.children[0]
    assert RUNNING_ICON in str(node.label)

    instance.sidebar.set_running(set())
    assert RUNNING_ICON not in str(node.label)


async def test_settings_modal_opens_and_previews_resolution(app):
    from avdmanager.ui.dialogs import SettingsModal

    instance, pilot = app
    instance.action_settings()
    await pilot.pause()
    assert isinstance(instance.screen, SettingsModal)
    assert instance.screen.query_one("#cfg-sdk_path", Input) is not None


# --------------------------------------------------------------------------- #
# Actions that shell out
# --------------------------------------------------------------------------- #


async def test_reveal_action_awaits_the_opener(app, monkeypatch):
    """Regression: a sync action that unpacked a Future crashed with
    'await wasn't used with future'."""
    from avdmanager.ui import app as app_module

    seen: list[str] = []

    async def fake_reveal(path: str):
        seen.append(path)
        return True, f"Opened {path}"

    monkeypatch.setattr(app_module, "reveal_in_file_manager", fake_reveal)

    instance, pilot = app
    instance.sidebar.avd_tree.focus()
    await pilot.press("down")
    await pilot.pause()

    await instance.action_reveal()
    await pilot.pause()

    assert seen == ["/home/u/.android/avd/Pixel_36_Ultra.avd"]


async def test_edit_config_uses_editor_when_available(app, monkeypatch):
    from avdmanager.ui import app as app_module

    opened: list[str] = []

    def fake_editor(path: str, editor: str = ""):
        opened.append(path)
        return True, f"Opened {path}"

    async def should_not_run(path: str):
        raise AssertionError("fallback must not run when $EDITOR works")

    monkeypatch.setattr(app_module, "open_in_editor", fake_editor)
    monkeypatch.setattr(app_module, "reveal_in_file_manager", should_not_run)

    instance, pilot = app
    instance.sidebar.avd_tree.focus()
    await pilot.press("down")
    await pilot.pause()

    await instance.action_edit_config()
    await pilot.pause()

    assert opened == ["/home/u/.android/avd/Pixel_36_Ultra.avd/config.ini"]


async def test_edit_config_falls_back_to_the_desktop_opener(app, monkeypatch):
    from avdmanager.ui import app as app_module

    fallbacks: list[str] = []

    def no_editor(path: str, editor: str = ""):
        return False, "Set $EDITOR or configure one in Settings."

    async def fake_reveal(path: str):
        fallbacks.append(path)
        return True, f"Opened {path}"

    monkeypatch.setattr(app_module, "open_in_editor", no_editor)
    monkeypatch.setattr(app_module, "reveal_in_file_manager", fake_reveal)

    instance, pilot = app
    instance.sidebar.avd_tree.focus()
    await pilot.press("down")
    await pilot.pause()

    await instance.action_edit_config()
    await pilot.pause()

    assert fallbacks == ["/home/u/.android/avd/Pixel_36_Ultra.avd/config.ini"]


async def test_reveal_without_a_selection_notifies(app):
    instance, pilot = app
    instance.sidebar.focus_tree(2)  # a package tree, so no AVD is selected
    instance.sidebar.tools_tree.root.children[0].expand()
    await pilot.pause()
    await instance.action_reveal()
    await pilot.pause()
    assert instance.statusbar._message == "Select an AVD first."


async def test_ctrl_c_cancels_the_running_job(app):
    """Regression: `runner.cancel` was called without being awaited."""
    instance, pilot = app
    import asyncio

    job = await instance.ctx.runner.start(
        ["sleep", "30"], label="test job", mutation=False
    )
    await asyncio.sleep(0.2)

    await instance.action_interrupt_cancel()
    await asyncio.wait_for(job._done.wait(), timeout=10)

    assert job.status.terminal
    assert not instance.ctx.runner.active_jobs


async def test_ctrl_c_with_no_job_exits(app):
    instance, _pilot = app
    assert instance.ctx.runner.active_jobs == []
    await instance.action_interrupt_cancel()
    assert instance._exit is True


# --------------------------------------------------------------------------- #
# Pure helpers used by the UI
# --------------------------------------------------------------------------- #


def test_sidebar_grouping_and_labels():
    from avdmanager.ui.sidebar import _group, _package_label

    packages = [
        Package("platforms/android-34", "1", "Android 34", installed=True),
        Package("platforms/android-36", "1", "Android 36"),
    ]
    grouped = _group(packages)
    # newest API level first
    assert list(grouped)[0].startswith("Android 16")

    installed = _package_label(packages[0])
    assert INSTALLED_ICON in installed
    available = _package_label(packages[1])
    assert AVAILABLE_ICON in available


def test_package_label_marks_updates():
    from avdmanager.ui.sidebar import UPDATE_ICON, _package_label

    label = _package_label(Package("build-tools/34.0.0", "34.0.0", "BT", installed=True, updates=2))
    assert UPDATE_ICON in label


def test_log_pane_buffers_lines():
    from avdmanager.ui.logpane import LogPane

    pane = LogPane()
    for i in range(1000):
        pane.push(f"line {i}")
    assert len(pane._buffer) == 1000  # not yet written to the widget


def test_log_pane_buffer_is_capped():
    from avdmanager.ui.logpane import MAX_BUFFER, LogPane

    pane = LogPane()
    for i in range(MAX_BUFFER + 500):
        pane.push(f"line {i}")
    assert len(pane._buffer) <= MAX_BUFFER


@pytest.mark.parametrize(
    "path",
    ["platforms/android-36", "system-images/android-36/google_apis/x86_64", "sources/android-36"],
)
def test_is_platform_helper(path):
    from avdmanager.ui.app import _is_platform

    assert _is_platform(Package(path))


@pytest.mark.parametrize("path", ["build-tools/36.0.0", "emulator", "ndk/28.2.1"])
def test_is_tool_helper(path):
    from avdmanager.ui.app import _is_platform

    assert not _is_platform(Package(path))


def test_validate_new_name_ignores_current():
    from avdmanager.ui.app import _validate_new_name

    avds = [Avd(name="Pixel"), Avd(name="Tablet")]
    assert _validate_new_name("Pixel", "Pixel", avds) is None
    assert _validate_new_name("Tablet", "Pixel", avds) is not None
    assert _validate_new_name("Fresh", "Pixel", avds) is None