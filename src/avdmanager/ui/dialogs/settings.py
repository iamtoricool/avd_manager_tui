"""Settings screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static

from avdmanager.config import Config, ResolvedPaths

#: (config attribute, label, placeholder) for each text field.
TEXT_FIELDS = (
    ("sdk_path", "Android SDK root", "/path/to/Android/Sdk"),
    ("avd_home", "AVD home", "leave blank for the system default"),
    ("cmdline_tools_version", "Command-line tools version", "latest"),
    ("avdmanager_path", "avdmanager executable", "auto-detected"),
    ("sdkmanager_path", "sdkmanager executable", "auto-detected"),
    ("android_cli_path", "android CLI executable", "auto-detected"),
    ("emulator_path", "emulator executable", "auto-detected"),
    ("adb_path", "adb executable", "auto-detected"),
    ("emulator_opts", "Default emulator options", "-no-snapshot-save -no-boot-anim"),
)


class SettingsModal(ModalScreen[Config]):
    """Edit configuration and see the resolved result live."""

    DEFAULT_CSS = """
    SettingsModal { align: center middle; }
    SettingsModal #dialog-body { width: 88; height: 80%; padding: 1 2;
                                 background: $surface; border: round $accent; }
    SettingsModal .dialog-title { text-style: bold; color: $accent; }
    SettingsModal .field-label { color: $text-muted; padding-top: 1; }
    SettingsModal .field-value { height: 3; }
    SettingsModal #resolved { height: auto; padding-top: 1;
                              border-top: solid $primary; }
    SettingsModal .dialog-buttons { height: auto; padding-top: 1;
                                    align-horizontal: right; }
    SettingsModal Button { margin-left: 2; }
    """

    BINDINGS = [("escape", "cancel", "Close")]

    def __init__(self, config: Config, paths: ResolvedPaths, config_path: Path) -> None:
        super().__init__()
        self._config = config
        self._paths = paths
        self._config_path = config_path

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-body"):
            yield Static("Settings", classes="dialog-title")
            with VerticalScroll():
                for attr, label, placeholder in TEXT_FIELDS:
                    yield Static(label, classes="field-label")
                    yield Input(
                        value=getattr(self._config, attr),
                        placeholder=placeholder,
                        id=f"cfg-{attr}",
                    )
                yield Static("SDK command-line tool", classes="field-label")
                yield Checkbox(
                    "Prefer the modern `android` CLI over `sdkmanager`",
                    value=self._config.prefer_android_cli,
                    id="cfg-prefer_android_cli",
                )
                yield Static("", id="resolved")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Close", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def on_mount(self) -> None:
        self._show_resolved()
        self.query_one("#cfg-sdk_path", Input).focus()

    # ------------------------------------------------------------------ #

    def _current(self) -> Config:
        """Build a Config from the current field values."""
        values = dict(self._config.to_dict())
        for attr, _label, _placeholder in TEXT_FIELDS:
            values[attr] = self.query_one(f"#cfg-{attr}", Input).value.strip()
        values["prefer_android_cli"] = self.query_one(
            "#cfg-prefer_android_cli", Checkbox
        ).value
        return Config(**values)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._show_resolved()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._show_resolved()

    def _show_resolved(self) -> None:
        """Preview what the current field values resolve to."""
        from avdmanager.config import resolve

        try:
            resolved = resolve(self._current())
        except Exception as exc:  # pragma: no cover - defensive
            self.query_one("#resolved", Static).update(f"Invalid configuration: {exc}")
            return

        rows = ["", "Resolved:", ""]
        for label, value, source in resolved.detail_rows():
            rows.append(f"  {label:12} {value}  ({source})")
        if resolved.issues:
            rows.append("")
            rows.append("Issues:")
            rows.extend(f"  - {issue}" for issue in resolved.issues)
        self.query_one("#resolved", Static).update("\n".join(rows))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.dismiss(self._current())
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)