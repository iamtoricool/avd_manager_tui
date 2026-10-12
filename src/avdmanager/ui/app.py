"""The Textual application."""

from __future__ import annotations

import asyncio

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Tree

from avdmanager import config as config_mod
from avdmanager.context import AppContext
from avdmanager.models import Avd, Package
from avdmanager.runner import Job
from avdmanager.services import CreateRequest, EmulatorState
from avdmanager.services.avd import open_in_editor, reveal_in_file_manager
from avdmanager.ui.detail import DetailPane
from avdmanager.ui.dialogs import (
    ConfirmModal,
    CreateAvdModal,
    PromptModal,
    SettingsModal,
)
from avdmanager.ui.logpane import LogPane
from avdmanager.ui.sidebar import Sidebar
from avdmanager.ui.statusbar import StatusBar

#: Debounce for the package filter, in seconds.
FILTER_DEBOUNCE = 0.35

CMDLINE_TOOLS_URL = "https://developer.android.com/studio#command-tools"


class AvdManagerApp(App):
    """Terminal UI for the Android SDK."""

    CSS_PATH = "theme.tcss"
    TITLE = "AVD Manager"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh_all", "Refresh"),
        ("n", "new_avd", "New AVD"),
        ("l", "launch", "Launch"),
        ("e", "rename", "Rename"),
        ("d", "delete", "Delete"),
        ("i", "install", "Install"),
        ("u", "uninstall", "Uninstall"),
        ("R", "reveal", "Reveal folder"),
        ("c", "edit_config", "Edit config.ini"),
        ("f", "focus_filter", "Filter"),
        ("s", "settings", "Settings"),
        ("L", "accept_licenses", "Accept licenses"),
        ("U", "update_all", "Update all"),
        ("F4", "toggle_log", "Toggle log"),
        ("question_mark", "help", "Help"),
        ("j", "focus_next_tree", "Next tree"),
        ("k", "focus_prev_tree", "Previous tree"),
    ]

    def __init__(self, context: AppContext | None = None) -> None:
        super().__init__()
        self.ctx = context or AppContext(
            runner=_default_runner(),
            config=config_mod.Config(),
            config_path=config_mod.default_config_path(),
        )
        self._filter_timer = None  # textual Timer
        self._pending_filter = ""
        self._avds: list[Avd] = []
        self._packages: list[Package] = []
        self._devices: list = []
        self._active_jobs: dict[int, Job] = {}

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="body"):
            yield Sidebar(id="sidebar")
            yield DetailPane(id="detail")
            yield LogPane(id="logpane")
        yield StatusBar(self.ctx.runner, id="statusbar")
        yield Input(placeholder="Filter packages…", id="filter")
        yield Footer()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        self._install_runner_hooks()
        self._apply_status()
        self.sidebar.set_avds_placeholder()
        self.sidebar.set_packages_placeholder()
        self.detail.show_empty("Loading…")
        self.sidebar.focus_tree(0)

        self.query_one("#filter", Input).display = False
        self.set_interval(2.0, self._poll_emulators)

        if self.ctx.backend_error:
            self.notify(self.ctx.backend_error, seconds=8)
            self.detail.show_error(self.ctx.backend_error)
        self.action_refresh_all()

    def on_unmount(self) -> None:
        self.ctx.runner._on_start = None
        self.ctx.runner._on_line = None
        self.ctx.runner._on_finish = None

    # ------------------------------------------------------------------ #
    # Convenience accessors
    # ------------------------------------------------------------------ #

    @property
    def sidebar(self) -> Sidebar:
        return self.query_one("#sidebar", Sidebar)

    @property
    def detail(self) -> DetailPane:
        return self.query_one("#detail", DetailPane)

    @property
    def logpane(self) -> LogPane:
        return self.query_one("#logpane", LogPane)

    @property
    def statusbar(self) -> StatusBar:
        return self.query_one("#statusbar", StatusBar)

    @property
    def filter_input(self) -> Input:
        return self.query_one("#filter", Input)

    @property
    def ready(self) -> bool:
        return self.ctx.sdk is not None and self.ctx.emulator is not None

    # ------------------------------------------------------------------ #
    # Runner wiring
    # ------------------------------------------------------------------ #

    def _install_runner_hooks(self) -> None:
        self.ctx.runner._on_line = lambda text, kind: self.logpane.push(text, kind)
        self.ctx.runner._on_finish = self._on_job_finished

    def _on_job_finished(self, job: Job) -> None:
        self._active_jobs.pop(job.id, None)
        self.logpane.push(f"--- {job.label}: {job.status.value} in {job.duration:.1f}s", "info")

    # ------------------------------------------------------------------ #
    # Loading (background workers)
    # ------------------------------------------------------------------ #

    @work(exclusive=False, group="load")
    async def _load_avds(self) -> None:
        if not self.ready:
            return
        self.logpane.push("Loading AVDs…", "info")
        try:
            self._avds = await self.ctx.avd.list_avds()
        except Exception as exc:
            self.logpane.push(f"AVD listing failed: {exc}", "err")
            self._avds = []
        await self.sidebar.set_avds(self._avds)
        self.logpane.push(f"Loaded {len(self._avds)} AVD(s)", "info")

    @work(exclusive=False, group="load")
    async def _load_packages(self, pattern: str = "") -> None:
        if not self.ready:
            return
        label = f"packages matching {pattern!r}" if pattern else "packages"
        self.logpane.push(f"Loading {label}…", "info")
        try:
            load = await self.ctx.sdk.load(pattern=pattern or None)
        except Exception as exc:
            self.logpane.push(f"Package listing failed: {exc}", "err")
            return

        if not load.ok:
            self.logpane.push(load.error, "err")
            self.detail.show_error(load.error)
            return

        self._packages = load.index.merged()
        await self.sidebar.set_packages(
            (p for p in self._packages if _is_platform(p)),
            (p for p in self._packages if not _is_platform(p)),
        )
        installed = sum(1 for p in self._packages if p.installed)
        self.logpane.push(
            f"Loaded {len(self._packages)} packages ({installed} installed)", "info"
        )

    @work(exclusive=False, group="load")
    async def _load_devices(self) -> None:
        if not self.ready:
            return
        try:
            self._devices = await self.ctx.avd.list_devices()
        except Exception:
            self._devices = []

    @work(exclusive=False, group="load")
    async def _poll_emulators(self) -> None:
        if not self.ready:
            return
        await self.ctx.emulator.refresh_devices()

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def action_refresh_all(self) -> None:
        self.sidebar.set_avds_placeholder()
        self.sidebar.set_packages_placeholder()
        self._load_avds()
        self._load_packages(self._pending_filter)

    def action_focus_next_tree(self) -> None:
        self.sidebar.focus_tree(self._tree_index() + 1)

    def action_focus_prev_tree(self) -> None:
        self.sidebar.focus_tree(self._tree_index() - 1)

    def _tree_index(self) -> int:
        trees = self.sidebar.trees
        for index, tree in enumerate(trees):
            if tree.has_focus:
                return index
        return 0

    def action_toggle_log(self) -> None:
        self.logpane.toggle()

    def action_focus_filter(self) -> None:
        widget = self.filter_input
        widget.display = True
        widget.focus()

    def action_help(self) -> None:
        self.notify(_HELP_TEXT, seconds=10)

    # -- settings -------------------------------------------------------- #

    def action_settings(self) -> None:
        self.push_screen(
            SettingsModal(self.ctx.config, self.ctx.paths, self.ctx.config_path),
            self._apply_settings,
        )

    def _apply_settings(self, config) -> None:
        if config is None:
            return
        self.ctx.config = config
        self.ctx.save_config()
        self.ctx.rebuild()
        self._apply_status()
        self.logpane.push("Settings reloaded", "info")
        self.action_refresh_all()

    def _apply_status(self) -> None:
        self.statusbar.set_backend(self.ctx.backend_label)
        self.statusbar.set_health(self.ctx.paths.issues)
        if self.ctx.paths.issues:
            self.logpane.push(
                f"SDK issues: {'; '.join(self.ctx.paths.issues)}", "info"
            )

    # -- AVD actions ------------------------------------------------------ #

    async def action_new_avd(self) -> None:
        if not self.ready:
            return
        await self._load_devices_call()
        images = [p for p in self._packages if p.category == "system-images" and p.installed]
        if not images:
            self.notify("No system images installed. Install one from SDK Platforms first.")
            return

        def on_result(result) -> None:
            if result is None:
                return
            self._create_avd(result.name, result.package_path, result.device_id, result.sdcard)

        await self.push_screen(
            CreateAvdModal(
                images,
                self._devices,
                existing_names={a.name for a in self._avds},
                avd_home=self.ctx.paths.avd_home,
            ),
            on_result,
        )

    async def _load_devices_call(self) -> None:
        if self._devices:
            return
        if self.ready:
            self._devices = await self.ctx.avd.list_devices()

    @work(exclusive=False, group="avd")
    async def _create_avd(self, name: str, package: str, device_id, sdcard: str) -> None:
        job = await self.ctx.avd.create(
            CreateRequest(
                name=name,
                package_path=package,
                device_id=device_id if device_id is None else int(device_id),
                sdcard=sdcard,
                avd_home=self.ctx.paths.avd_home,
                force=True,
            ),
            on_line=lambda text, kind: self.logpane.push(text, kind),
        )
        self._active_jobs[job.id] = job
        await self.ctx.runner.wait(job)
        self._load_avds()

    async def action_rename(self) -> None:
        avd = self.sidebar.selected_avd()
        if avd is None:
            self.notify("Select an AVD to rename.")
            return

        def on_result(new_name) -> None:
            if new_name:
                self._rename_avd(avd.name, new_name)

        await self.push_screen(
            PromptModal(
                f"Rename AVD {avd.name}",
                placeholder="New name",
                initial=avd.name,
                validate=lambda name: _validate_new_name(name, avd.name, self._avds),
            ),
            on_result,
        )

    @work(exclusive=False, group="avd")
    async def _rename_avd(self, old: str, new: str) -> None:
        job = await self.ctx.avd.rename(old, new)
        self._active_jobs[job.id] = job
        await self.ctx.runner.wait(job)
        self._load_avds()

    async def action_delete(self) -> None:
        avd = self.sidebar.selected_avd()
        if avd is None:
            self.notify("Select an AVD to delete.")
            return
        detail = [f"Name: {avd.name}", f"Path: {avd.path}", f"Image: {avd.based_on}"]
        await self.push_screen(
            ConfirmModal(f"Delete AVD {avd.name}?", "This cannot be undone.", detail=detail),
            lambda confirmed: self._delete_avd(avd.name) if confirmed else None,
        )

    @work(exclusive=False, group="avd")
    async def _delete_avd(self, name: str) -> None:
        job = await self.ctx.avd.delete(name)
        self._active_jobs[job.id] = job
        await self.ctx.runner.wait(job)
        self._load_avds()

    async def action_launch(self) -> None:
        if not self.ready:
            return
        avd = self.sidebar.selected_avd()
        if avd is None:
            self.notify("Select an AVD to launch.")
            return
        state = self.ctx.emulator.state_of(avd.name)
        if state is EmulatorState.RUNNING:
            self.notify(f"{avd.name} is already running.")
            return
        opts = config_mod.parse_emulator_opts(self.ctx.config.emulator_opts)
        self.logpane.push(f"Launching {avd.name}…", "info")
        job = await self.ctx.emulator.start(
            avd.name, opts, on_line=lambda text, kind: self.logpane.push(text, kind)
        )
        self._active_jobs[job.id] = job
        self.notify(f"Emulator {avd.name} starting…")

    def action_reveal(self) -> None:
        avd = self.sidebar.selected_avd()
        if avd is None or not avd.path:
            self.notify("Select an AVD first.")
            return
        ok, message = _run_awaitable(reveal_in_file_manager(avd.path))
        self.notify(message)

    def action_edit_config(self) -> None:
        avd = self.sidebar.selected_avd()
        if avd is None or not avd.config_ini:
            self.notify("Select an AVD first.")
            return
        ok, message = open_in_editor(avd.config_ini)
        if not ok:
            # Fall back to the desktop opener before giving up.
            ok, message = _run_awaitable(reveal_in_file_manager(avd.config_ini))
        self.notify(message)

    # -- package actions -------------------------------------------------- #

    async def action_install(self) -> None:
        pkg = self.sidebar.selected_package()
        if pkg is None:
            self.notify("Select a package to install.")
            return
        if pkg.installed:
            self.notify(f"{pkg.path} is already installed.")
            return
        detail = [
            f"Package: {pkg.path}",
            f"Version: {pkg.version}",
            "",
            "Size is determined during download.",
        ]
        await self.push_screen(
            ConfirmModal(
                f"Install {pkg.description or pkg.path}?",
                "The package will be downloaded into your SDK.",
                detail=detail,
                confirm_label="Install",
                danger=False,
            ),
            lambda ok: self._install([pkg.path]) if ok else None,
        )

    async def action_uninstall(self) -> None:
        pkg = self.sidebar.selected_package()
        if pkg is None:
            self.notify("Select an installed package to uninstall.")
            return
        if not pkg.installed:
            self.notify(f"{pkg.path} is not installed.")
            return
        detail = [
            f"Package: {pkg.path}",
            f"Version: {pkg.version}",
            f"Category: {pkg.category}",
            "",
            "Any AVDs built on this image will stop working.",
        ]
        await self.push_screen(
            ConfirmModal(
                f"Uninstall {pkg.description or pkg.path}?",
                "This removes the package from your SDK.",
                detail=detail,
                confirm_label="Uninstall",
            ),
            lambda ok: self._remove([pkg.path]) if ok else None,
        )

    @work(exclusive=False, group="sdk")
    async def _install(self, packages: list[str]) -> None:
        job = await self.ctx.sdk.install(
            packages, on_line=lambda text, kind: self.logpane.push(text, kind)
        )
        self._active_jobs[job.id] = job
        await self.ctx.runner.wait(job)
        self._load_packages(self._pending_filter)

    @work(exclusive=False, group="sdk")
    async def _remove(self, packages: list[str]) -> None:
        job = await self.ctx.sdk.remove(
            packages, on_line=lambda text, kind: self.logpane.push(text, kind)
        )
        self._active_jobs[job.id] = job
        await self.ctx.runner.wait(job)
        self._load_packages(self._pending_filter)

    def action_accept_licenses(self) -> None:
        self._accept_licenses()

    @work(exclusive=False, group="sdk")
    async def _accept_licenses(self) -> None:
        job = await self.ctx.sdk.accept_licenses(
            on_line=lambda text, kind: self.logpane.push(text, kind)
        )
        self._active_jobs[job.id] = job
        await self.ctx.runner.wait(job)
        self.notify("Licence acceptance finished.")

    def action_update_all(self) -> None:
        self._update_all()

    @work(exclusive=False, group="sdk")
    async def _update_all(self) -> None:
        job = await self.ctx.sdk.update_all(
            on_line=lambda text, kind: self.logpane.push(text, kind)
        )
        self._active_jobs[job.id] = job
        await self.ctx.runner.wait(job)
        self._load_packages(self._pending_filter)

    # -- stopping -------------------------------------------------------- #

    def action_interrupt_cancel(self) -> None:
        """Ctrl+C cancels the newest running job, or quits when idle."""
        job = self.statusbar.cancel_focused_job()
        if job is None:
            self.exit()
            return
        self.ctx.runner.cancel(job)
        self.notify(f"Cancelling {job.label}…")

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #

    @on(Tree.NodeHighlighted, "#avd-tree, #platforms-tree, #tools-tree")
    def _tree_highlighted(self, event: Tree.NodeHighlighted) -> None:
        node = event.node
        data = node.data if node is not None else None
        if not isinstance(data, tuple) or len(data) != 2:
            return
        kind, payload = data
        if kind == "avd":
            avd = payload
            footer = "enter launch · e rename · d delete · R reveal folder · c config.ini"
            self.detail.show_rows(avd.name, avd.detail_rows(), footer=footer)
        elif kind == "pkg":
            pkg = payload
            footer = "u uninstall" if pkg.installed else "i install"
            self.detail.show_rows(
                pkg.description or pkg.path, pkg.detail_rows(), footer=footer
            )
        elif kind == "pkg-group":
            self.detail.show_rows(payload, [("Group", payload)], footer=status_hint())

    @on(Input.Changed, "#filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        self._pending_filter = event.value.strip()
        if self._filter_timer is not None:
            self._filter_timer.stop()
        # Debounce: `android sdk list <pattern>` filters server-side, so this is
        # cheap, but a fast typist would still spawn a process per keystroke.
        self._filter_timer = self.set_timer(FILTER_DEBOUNCE, self._apply_filter)

    def _apply_filter(self) -> None:
        self._load_packages(self._pending_filter)


def _is_platform(pkg: Package) -> bool:
    return pkg.category in ("platforms", "system-images", "sources")


def _validate_new_name(name: str, current: str, avds: list[Avd]) -> str | None:
    from avdmanager.services.avd import validate_avd_name

    names = {a.name for a in avds} - {current}
    return validate_avd_name(name, names)


def status_hint() -> str:
    return "i install · u uninstall · f filter"


def _run_awaitable(coro):
    """Run a coroutine from a synchronous action handler."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return asyncio.ensure_future(coro)
    return asyncio.run(coro)


def _default_runner():
    from avdmanager.runner import JobRunner

    return JobRunner()


_HELP_TEXT = (
    "AVD Manager keys — n new AVD · l launch · e rename · d delete · "
    "R reveal folder · c edit config.ini · i install · u uninstall · "
    "f filter · r refresh · L accept licences · U update all · "
    "s settings · j/k switch tree · F4 toggle log · ctrl+c cancel job · q quit"
)