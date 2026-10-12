"""Create-AVD wizard.

Three steps driven from one screen: name, system image, hardware profile (and
an optional sdcard size). Only installed system images are offered, because
``avdmanager create avd -k`` cannot install anything itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from avdmanager.models import Device, Package
from avdmanager.services.avd import validate_avd_name


@dataclass
class CreateResult:
    """What the wizard collected."""

    name: str
    package_path: str
    device_id: int | None
    sdcard: str


class CreateAvdModal(ModalScreen[CreateResult | None]):
    """Guided AVD creation."""

    DEFAULT_CSS = """
    CreateAvdModal { align: center middle; }
    CreateAvdModal #dialog-body { width: 84; height: 80%; padding: 1 2;
                                   background: $surface; border: round $accent; }
    CreateAvdModal .dialog-title { text-style: bold; color: $accent; }
    CreateAvdModal .dialog-hint { color: $text-muted; padding-bottom: 1; }
    CreateAvdModal .step-title { color: $warning; text-style: bold;
                                 padding-top: 1; }
    CreateAvdModal ListView { height: 1fr; border: round $primary;
                              overflow-y: auto; margin-top: 1; }
    CreateAvdModal .dialog-error { color: $error; height: auto; padding-top: 1; }
    CreateAvdModal .dialog-buttons { height: auto; padding-top: 1;
                                     align-horizontal: right; }
    CreateAvdModal Button { margin-left: 2; }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("down", "next_or_down", "Next"),
        ("enter", "confirm", "Confirm"),
    ]

    def __init__(
        self,
        images: list[Package],
        devices: list[Device],
        *,
        existing_names: set[str] | None = None,
        avd_home: str = "",
        default_sdcard: str = "512M",
    ) -> None:
        super().__init__()
        self._images = [p for p in images if p.category == "system-images"]
        self._devices = devices
        self._existing = existing_names or set()
        self._avd_home = avd_home
        self._default_sdcard = default_sdcard

        self._step = 0
        self._name = ""
        self._image: Package | None = None
        self._device_id: int | None = None
        self._device_label = "Default (no hardware profile)"
        self._sdcard = default_sdcard

    # ------------------------------------------------------------------ #
    # Composition
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-body"):
            yield Static("Create Android Virtual Device", classes="dialog-title")
            yield Static(
                "Pick a name, a system image, and optionally a hardware profile.",
                classes="dialog-hint",
            )
            yield Vertical(id="wizard-body")
            yield Static("", classes="dialog-error", id="wizard-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Back", id="back")
                yield Button("Next", id="next", variant="primary")

    def on_mount(self) -> None:
        self._render()

    # ------------------------------------------------------------------ #
    # Steps
    # ------------------------------------------------------------------ #

    def _render(self) -> None:
        body = self.query_one("#wizard-body", Vertical)
        body.remove_children()
        error = self.query_one("#wizard-error", Static)
        error.update("")
        back = self.query_one("#back", Button)
        next_button = self.query_one("#next", Button)

        if self._step == 0:
            body.mount(
                Static("1. Name", classes="step-title"),
                Static(
                    "Letters, digits and underscores only. Must be unique.",
                    classes="dialog-hint",
                ),
                Input(placeholder="MyAVD", id="wizard-name"),
            )
            self.query_one("#wizard-name", Input).focus()
            next_button.label = "Next"
            back.display = False

        elif self._step == 1:
            body.mount(
                Static("2. System image", classes="step-title"),
                Static(
                    f"Only installed images are listed ({len(self._images)} found).",
                    classes="dialog-hint",
                ),
                ListView(
                    *[
                        ListItem(
                            Label(
                                f"{pkg.description}\n"
                                f"[detail-value]{pkg.path}  ({pkg.version})[/]"
                            )
                        )
                        for pkg in self._images
                    ],
                    id="wizard-images",
                ),
            )
            view = self.query_one("#wizard-images", ListView)
            if self._images:
                view.index = 0
                view.focus()
            next_button.label = "Next"
            back.display = True

        elif self._step == 2:
            body.mount(
                Static("3. Hardware profile", classes="step-title"),
                Static(
                    "Optional. The default profile matches the system image.",
                    classes="dialog-hint",
                ),
                Input(
                    placeholder="Type to filter devices, e.g. pixel_7",
                    id="wizard-device",
                ),
                ListView(id="wizard-devices"),
                Static("SD card size", classes="step-title"),
                Input(value=self._default_sdcard, placeholder="512M", id="wizard-sdcard"),
            )
            self._render_devices("")
            self.query_one("#wizard-device", Input).focus()
            next_button.label = "Create"
            back.display = True

    def _render_devices(self, needle: str) -> None:
        view = self.query_one("#wizard-devices", ListView)
        view.clear()
        lowered = needle.strip().lower()
        entries: list[tuple[str, int | None]] = [("Default (no hardware profile)", None)]
        for device in self._devices:
            haystack = f"{device.name} {device.oem} {device.tag} {device.id_name}".lower()
            if not lowered or lowered in haystack:
                entries.append((f"{device.name}  ·  {device.summary}", device.id))

        if len(entries) == 1:
            view.append(ListItem(Label("No matching hardware profile.")))
            return
        for label, device_id in entries:
            item = ListItem(Label(label))
            item.device_id = device_id
            view.append(item)
        view.index = 0

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "back":
            self._step = max(0, self._step - 1)
            self._render()
        else:
            self.action_next_or_down()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_next_or_down()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "wizard-device":
            self._render_devices(event.value)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if item is None:
            return
        device_id = getattr(item, "device_id", None)
        if device_id is not None or isinstance(getattr(item, "device_id", None), int):
            self._device_id = item.device_id

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_next_or_down()

    def action_next_or_down(self) -> None:
        error = self._advance()
        if error:
            self.query_one("#wizard-error", Static).update(error)
            return
        self.query_one("#wizard-error", Static).update("")
        if self._step > 2:
            self.action_confirm()

    def _advance(self) -> str:
        if self._step == 0:
            name = self.query_one("#wizard-name", Input).value.strip()
            error = validate_avd_name(name, self._existing)
            if error:
                return error
            self._name = name
            self._step = 1
            self._render()
            return ""

        if self._step == 1:
            view = self.query_one("#wizard-images", ListView)
            index = view.index or 0
            if not self._images:
                return "No system images are installed. Install one from SDK Platforms first."
            self._image = self._images[min(index, len(self._images) - 1)]
            self._step = 2
            self._render()
            return ""

        self._sdcard = self.query_one("#wizard-sdcard", Input).value.strip()
        return ""

    def action_confirm(self) -> None:
        if not self._name or self._image is None:
            return
        self.dismiss(
            CreateResult(
                name=self._name,
                package_path=self._image.path,
                device_id=self._device_id,
                sdcard=self._sdcard,
            )
        )

    def action_cancel(self) -> None:
        self.dismiss(None)