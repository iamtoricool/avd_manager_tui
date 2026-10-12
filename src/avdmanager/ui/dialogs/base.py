"""Modal dialogs."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

# --------------------------------------------------------------------------- #
# Confirmation
# --------------------------------------------------------------------------- #


class ConfirmModal(ModalScreen[bool]):
    """Yes/no confirmation.

    Deletion is always routed through here, and the body spells out exactly
    what will be removed so nothing is destroyed by a stray keypress.
    """

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal #dialog-body { width: 72; height: auto; padding: 1 2;
                               background: $surface; border: round $accent; }
    ConfirmModal .dialog-title { text-style: bold; color: $accent; }
    ConfirmModal .confirm-warning { color: $warning; text-style: bold; }
    ConfirmModal .dialog-buttons { height: auto; padding-top: 1;
                                   align-horizontal: right; }
    ConfirmModal Button { margin-left: 2; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        message: str,
        *,
        detail: list[str] | None = None,
        confirm_label: str = "Delete",
        danger: bool = True,
    ) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._detail = detail or []
        self._confirm_label = confirm_label
        self._danger = danger

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-body"):
            yield Static(self._title, classes="dialog-title")
            yield Static(self._message)
            if self._detail:
                yield Static("")
                for line in self._detail:
                    cls = "confirm-warning" if self._danger else "dialog-hint"
                    yield Static(line, classes=cls)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(
                    self._confirm_label,
                    id="confirm",
                    variant="error" if self._danger else "primary",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


# --------------------------------------------------------------------------- #
# Type-to-confirm
# --------------------------------------------------------------------------- #


class TypeToConfirmModal(ModalScreen[bool]):
    """Confirmation that requires typing an exact phrase.

    Used for irreversible, wide-reaching operations such as wiping every AVD,
    where a stray Enter should not be enough.
    """

    DEFAULT_CSS = """
    TypeToConfirmModal { align: center middle; }
    TypeToConfirmModal #dialog-body { width: 80; height: auto; padding: 1 2;
                                       background: $surface; border: round $error; }
    TypeToConfirmModal .dialog-title { text-style: bold; color: $error; }
    TypeToConfirmModal .dialog-hint { color: $text-muted; padding-bottom: 1; }
    TypeToConfirmModal .confirm-warning { color: $warning; text-style: bold; }
    TypeToConfirmModal .confirm-phrase { color: $accent; text-style: bold; }
    TypeToConfirmModal .field-label { color: $text-muted; padding-top: 1; }
    TypeToConfirmModal .dialog-error { color: $error; height: auto; padding-top: 1; }
    TypeToConfirmModal .dialog-buttons { height: auto; padding-top: 1;
                                         align-horizontal: right; }
    TypeToConfirmModal Button { margin-left: 2; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        message: str,
        *,
        detail: list[str] | None = None,
        phrase: str,
        confirm_label: str = "Wipe everything",
    ) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._detail = detail or []
        self._phrase = phrase
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-body"):
            yield Static(self._title, classes="dialog-title")
            yield Static(self._message)
            if self._detail:
                yield Static("")
                for line in self._detail:
                    yield Static(line, classes="confirm-warning")
            yield Static("")
            yield Static(
                f'Type [class="confirm-phrase"]{self._phrase}[/] to continue:',
                classes="field-label",
            )
            yield Input(placeholder=self._phrase, id="confirm-input")
            yield Static("", classes="dialog-error", id="confirm-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._confirm_label, id="confirm", variant="error", disabled=True)

    def on_mount(self) -> None:
        self.query_one("#confirm-input", Input).focus()

    def _matches(self, value: str) -> bool:
        return value.strip() == self._phrase

    def on_input_changed(self, event: Input.Changed) -> None:
        matched = self._matches(event.value)
        self.query_one("#confirm", Button).disabled = not matched
        self.query_one("#confirm-error", Static).update(
            "" if matched or not event.value.strip() else "Phrase does not match."
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._matches(event.value):
            self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            if self._matches(self.query_one("#confirm-input", Input).value):
                self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


# --------------------------------------------------------------------------- #
# Text input
# --------------------------------------------------------------------------- #


class PromptModal(ModalScreen[str | None]):
    """Single-line text input with live validation."""

    DEFAULT_CSS = """
    PromptModal { align: center middle; }
    PromptModal #dialog-body { width: 64; height: auto; padding: 1 2;
                               background: $surface; border: round $accent; }
    PromptModal .dialog-title { text-style: bold; color: $accent; }
    PromptModal .field-label { color: $text-muted; padding-top: 1; }
    PromptModal .dialog-error { color: $error; height: auto; padding-top: 1; }
    PromptModal .dialog-buttons { height: auto; padding-top: 1;
                                 align-horizontal: right; }
    PromptModal Button { margin-left: 2; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        *,
        placeholder: str = "",
        initial: str = "",
        validate=None,
        hint: str = "",
    ) -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial
        self._validate = validate
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-body"):
            yield Static(self._title, classes="dialog-title")
            if self._hint:
                yield Static(self._hint, classes="dialog-hint")
            yield Static("", classes="field-label")
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id="prompt-input",
            )
            yield Static("", classes="dialog-error", id="prompt-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="confirm", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        error = self._validate(event.value) if self._validate else None
        self.query_one("#prompt-error", Static).update(error or "")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        value = self.query_one("#prompt-input", Input).value.strip()
        error = self._validate(value) if self._validate else None
        if error:
            self.query_one("#prompt-error", Static).update(error)
            return
        self.dismiss(value)


# --------------------------------------------------------------------------- #
# Choice list
# --------------------------------------------------------------------------- #


class ChoiceModal(ModalScreen[str | None]):
    """Filterable single-choice list (devices, system images, ...)."""

    DEFAULT_CSS = """
    ChoiceModal { align: center middle; }
    ChoiceModal #dialog-body { width: 78; height: 24; padding: 1 2;
                               background: $surface; border: round $accent; }
    ChoiceModal .dialog-title { text-style: bold; color: $accent; }
    ChoiceModal .dialog-hint { color: $text-muted; padding-bottom: 1; }
    ChoiceModal .package-list { height: 1fr; border: round $primary;
                               overflow-y: auto; }
    ChoiceModal .dialog-buttons { height: auto; padding-top: 1;
                                 align-horizontal: right; }
    ChoiceModal Button { margin-left: 2; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        options: list[tuple[str, str]],
        *,
        hint: str = "",
    ) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._hint = hint

    def compose(self) -> ComposeResult:
        from textual.widgets import Input, OptionList

        with Vertical(id="dialog-body"):
            yield Static(self._title, classes="dialog-title")
            if self._hint:
                yield Static(self._hint, classes="dialog-hint")
            yield Input(placeholder="Filter…", id="choice-filter")
            yield OptionList(id="choice-list")
            yield Static("", classes="dialog-error", id="choice-error")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Select", id="confirm", variant="primary")

    def on_mount(self) -> None:
        from textual.widgets import OptionList

        self._rebuild("", OptionList(self._prompt()))
        self.query_one("#choice-filter", Input).focus()

    def _prompt(self) -> list[tuple[str, str]]:
        return [("Default (no hardware profile)", "")] + self._options

    def _rebuild(self, needle: str, option_list) -> None:

        option_list.clear_options()
        lowered = needle.strip().lower()
        matches = [
            (label, value)
            for label, value in self._prompt()
            if not lowered or lowered in label.lower() or lowered in value.lower()
        ]
        if not matches:
            option_list.add_option("No matches")
            return
        option_list.add_options(matches)

    def on_input_changed(self, event: Input.Changed) -> None:
        from textual.widgets import OptionList

        if event.input.id == "choice-filter":
            self._rebuild(event.value, self.query_one("#choice-list", OptionList))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_option_list_option_selected(self, event) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._submit()
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        from textual.widgets import OptionList

        option_list = self.query_one("#choice-list", OptionList)
        index = option_list.highlighted
        if index is None or index < 0 or index >= len(option_list._options):
            self.query_one("#choice-error", Static).update("Nothing selected.")
            return
        _, value = option_list.get_option_at_index(index)
        self.dismiss(value)
