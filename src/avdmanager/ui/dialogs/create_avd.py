"""Create-AVD wizard.

Three steps driven from one screen: name, system image, hardware profile (and
an optional sdcard size). Only installed system images are offered, because
``avdmanager create avd -k`` cannot install anything itself.

All three steps are built once in :meth:`compose` and shown or hidden by
toggling ``display``. An earlier revision rebuilt the step widgets on every
transition, calling ``remove_children()`` and then ``mount()`` in the same
synchronous block -- ``remove_children`` only *schedules* removal in Textual,
so re-registering the same widget IDs raised ``DuplicateIds`` the moment the
user advanced a step.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from avdmanager.models import Device, Package
from avdmanager.services.avd import validate_avd_name

#: ListItem id used for "no hardware profile".
DEFAULT_DEVICE_ID = "dev-default"


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
    CreateAvdModal .wizard-step { height: 1fr; }
    CreateAvdModal ListView { height: 1fr; border: round $primary;
                              overflow-y: auto; margin-top: 1; }
    CreateAvdModal #wizard-sdcard { height: 3; }
    CreateAvdModal .dialog-error { color: $error; height: auto; }
    CreateAvdModal .dialog-buttons { height: auto; padding-top: 1;
                                     align-horizontal: right; }
    CreateAvdModal Button { margin-left: 2; }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("down", "advance", "Next"),
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
        self._sdcard = default_sdcard

        #: ListItem id -> device id (``None`` for the default profile).
        self._device_by_item: dict[str, int | None] = {}

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
            with Vertical(id="wizard-body"):
                yield from self._step_name()
                yield from self._step_image()
                yield from self._step_device()
            yield Static("", classes="dialog-error", id="wizard-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Back", id="back")
                yield Button("Next", id="next", variant="primary")

    def _step_name(self) -> ComposeResult:
        with Vertical(classes="wizard-step", id="step-name"):
            yield Static("1. Name", classes="step-title")
            yield Static(
                "Letters, digits and underscores only. Must be unique.",
                classes="dialog-hint",
            )
            yield Input(placeholder="MyAVD", id="wizard-name")

    def _step_image(self) -> ComposeResult:
        with Vertical(classes="wizard-step", id="step-image"):
            yield Static("2. System image", classes="step-title")
            yield Static(
                f"Only installed images are listed ({len(self._images)} found).",
                classes="dialog-hint",
                id="wizard-image-count",
            )
            yield ListView(id="wizard-images")

    def _step_device(self) -> ComposeResult:
        with Vertical(classes="wizard-step", id="step-device"):
            yield Static("3. Hardware profile", classes="step-title")
            yield Static(
                "Optional. The default profile matches the system image.",
                classes="dialog-hint",
            )
            yield Input(placeholder="Type to filter devices, e.g. pixel_7", id="wizard-device")
            yield ListView(id="wizard-devices")
            yield Static("SD card size", classes="step-title")
            yield Input(value=self._default_sdcard, placeholder="512M", id="wizard-sdcard")

    def on_mount(self) -> None:
        self._populate_images()
        self._populate_devices()
        self._show_step(0)

    # ------------------------------------------------------------------ #
    # Populating (once, never re-mounted)
    # ------------------------------------------------------------------ #

    def _populate_images(self) -> None:
        view = self.query_one("#wizard-images", ListView)
        view.clear()
        if not self._images:
            view.append(ListItem(Label("No system images installed.")))
            return
        for pkg in self._images:
            view.append(
                ListItem(
                    Label(f"{pkg.description}\n[detail-value]{pkg.path}  ({pkg.version})[/]")
                )
            )
        view.index = 0

    def _populate_devices(self, needle: str = "") -> None:
        """Build every device item once; filtering only toggles visibility."""
        view = self.query_one("#wizard-devices", ListView)
        if not view.children:
            entries: list[tuple[str, str, int | None]] = [
                ("Default (no hardware profile)", DEFAULT_DEVICE_ID, None)
            ]
            for device in self._devices:
                label = f"{device.name}  ·  {device.summary}"
                entries.append((label, f"dev-{device.id}", device.id))

            for label, item_id, device_id in entries:
                item = ListItem(Label(label), id=item_id)
                self._device_by_item[item_id] = device_id
                view.append(item)

        lowered = needle.strip().lower()
        for item in view.children:
            device_id = self._device_by_item.get(item.id)
            if device_id is None:
                matches = True
            else:
                device = next((d for d in self._devices if d.id == device_id), None)
                haystack = (
                    f"{device.name} {device.oem} {device.tag} {device.id_name}"
                    if device
                    else ""
                ).lower()
                matches = not lowered or lowered in haystack
            item.display = matches

        visible = [i for i, item in enumerate(view.children) if item.display]
        view.index = visible[0] if visible else None
        self._sync_device(view)

    def _sync_device(self, view: ListView) -> None:
        index = view.index
        if index is None:
            self._device_id = None
            return
        node = view.children[index] if index < len(view.children) else None
        self._device_id = self._device_by_item.get(node.id) if node is not None else None

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    def _show_step(self, step: int) -> None:
        """Toggle which step is visible. No widgets are created or destroyed."""
        self._step = step
        for index, step_id in enumerate(("step-name", "step-image", "step-device")):
            self.query_one(f"#{step_id}", Vertical).display = index == step

        self.query_one("#back", Button).display = step > 0
        self.query_one("#next", Button).label = "Create" if step == 2 else "Next"
        self.query_one("#wizard-error", Static).update("")

        focus = {
            0: "#wizard-name",
            1: "#wizard-images",
            2: "#wizard-device",
        }[step]
        self.query_one(focus).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "back":
            self._show_step(self._step - 1)
        else:
            self.action_next_or_down()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "wizard-device":
            # Enter in the filter steps into the device list rather than
            # creating the AVD.
            self.query_one("#wizard-devices", ListView).focus()
            return
        self.action_next_or_down()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "wizard-device":
            self._populate_devices(event.value)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view is self.query_one("#wizard-devices", ListView):
            self._sync_device(event.list_view)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view is self.query_one("#wizard-images", ListView):
            # Picking an image moves on to the hardware profile.
            self.action_next_or_down()
        elif event.list_view is self.query_one("#wizard-devices", ListView):
            # Selecting a device only records it; the Create button submits.
            self._sync_device(event.list_view)

    def action_next_or_down(self) -> None:
        """The Next/Create button and Enter: advance, or submit on the last step."""
        if self._step >= 2:
            self._submit()
            return
        error = self._advance()
        if error:
            self.query_one("#wizard-error", Static).update(error)

    def action_advance(self) -> None:
        """The Down arrow: move forward, but never create the AVD.

        On the final step it steps into the hardware-profile list so a stray
        Down cannot submit the wizard.
        """
        if self._step >= 2:
            self.query_one("#wizard-devices", ListView).focus()
            return
        error = self._advance()
        if error:
            self.query_one("#wizard-error", Static).update(error)

    def _submit(self) -> None:
        """Collect the last field and dismiss with the result."""
        self._sdcard = self.query_one("#wizard-sdcard", Input).value.strip()
        self.action_confirm()

    def _advance(self) -> str:
        """Move between the real steps (0 -> 1 -> 2); never submits."""
        if self._step == 0:
            name = self.query_one("#wizard-name", Input).value.strip()
            error = validate_avd_name(name, self._existing)
            if error:
                return error
            self._name = name
            self._show_step(1)
            return ""

        if self._step == 1:
            if not self._images:
                return "No system images are installed. Install one from SDK Platforms first."
            view = self.query_one("#wizard-images", ListView)
            index = view.index or 0
            self._image = self._images[min(index, len(self._images) - 1)]
            self._show_step(2)
            return ""

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