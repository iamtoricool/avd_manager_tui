"""Sidebar with the three tree views.

Mirrors the reference extension's three views -- AVDs, SDK Platforms and SDK
Tools -- but each tree is grouped so the top level stays small (tens of nodes
rather than the ~650 flat package list). Children are attached incrementally
with a yield between batches so the UI keeps painting while a large tree loads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Iterator

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, Tree

from avdmanager.models import Avd, Package

#: How many nodes to add before yielding to the event loop.
BATCH = 60

INSTALLED_ICON = "[*]"
AVAILABLE_ICON = "[ ]"
UPDATE_ICON = "[!]"
MARK_ICON = ">"
RUNNING_ICON = "[>]"

#: Section header glyphs, matching Textual's own collapsible convention.
EXPANDED_GLYPH = "▼"
COLLAPSED_GLYPH = "▶"


class Selected(Message):
    """Emitted when the user highlights a node in any tree."""

    def __init__(self, kind: str, payload) -> None:
        super().__init__()
        self.kind = kind
        self.payload = payload


class SectionTitle(Static):
    """Clickable section header that collapses the tree beneath it.

    Collapsing hides the tree *widget* (rather than just its children, which is
    what the tree's own root triangle used to do), so the other sections grow
    into the freed rows. The header itself always stays visible.
    """

    def __init__(self, title: str, target: Tree, **kwargs) -> None:
        super().__init__(classes="section-title", **kwargs)
        self._title = title
        self._target = target
        self._collapsed = False
        self._refresh_label()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def on_click(self) -> None:
        self.toggle()

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse or expand the section, moving focus if it is hidden."""
        was_focused = self._target.has_focus
        self._collapsed = collapsed
        self._target.display = not collapsed
        self.set_class(collapsed, "-collapsed")
        self._refresh_label()
        if collapsed and was_focused:
            parent = self.parent
            if isinstance(parent, Sidebar):
                parent.focus_after(self._target)

    def _refresh_label(self) -> None:
        glyph = COLLAPSED_GLYPH if self._collapsed else EXPANDED_GLYPH
        self.update(f"{glyph} {self._title}")


class Sidebar(Vertical):
    """Three stacked tree views."""

    DEFAULT_CSS = """
    Sidebar { width: 42; min-width: 24; }
    Sidebar Tree { height: 1fr; min-height: 4; border: none; padding: 0 1;
                   background: transparent; }
    Sidebar Tree:focus { border: none; }
    Sidebar > .section-title { height: 1; padding: 0 1; color: $text-muted;
                               text-style: bold; background: $panel; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        #: Package paths marked for a batch operation.
        self._marked: set[str] = set()
        #: AVD names with a live emulator.
        self._running_avds: set[str] = set()
        # The trees are created up front so the section headers can target
        # them. The empty root row is hidden -- the header is the collapse
        # control now, not the tree's own triangle.
        self._avd_tree = Tree("", id="avd-tree")
        self._platforms_tree = Tree("", id="platforms-tree")
        self._tools_tree = Tree("", id="tools-tree")
        for tree in (self._avd_tree, self._platforms_tree, self._tools_tree):
            tree.show_root = False
        #: Section key -> its clickable header.
        self._sections: dict[str, SectionTitle] = {}

    def compose(self) -> ComposeResult:
        for key, title, tree in (
            ("avds", "AVDS", self._avd_tree),
            ("platforms", "SDK PLATFORMS", self._platforms_tree),
            ("tools", "SDK TOOLS", self._tools_tree),
        ):
            section = SectionTitle(title, tree, id=f"section-{key}")
            self._sections[key] = section
            yield section
            yield tree

    # ------------------------------------------------------------------ #
    # Trees
    # ------------------------------------------------------------------ #

    @property
    def avd_tree(self) -> Tree:
        return self._avd_tree

    @property
    def platforms_tree(self) -> Tree:
        return self._platforms_tree

    @property
    def tools_tree(self) -> Tree:
        return self._tools_tree

    @property
    def trees(self) -> tuple[Tree, Tree, Tree]:
        return (self._avd_tree, self._platforms_tree, self._tools_tree)

    def focus_tree(self, index: int) -> None:
        """Focus the tree at ``index`` when it is visible."""
        trees = self.trees
        if 0 <= index < len(trees) and trees[index].display:
            trees[index].focus()

    def focus_next_tree(self) -> None:
        """Focus the next visible tree, skipping collapsed sections."""
        self._focus_step(1)

    def focus_prev_tree(self) -> None:
        """Focus the previous visible tree, skipping collapsed sections."""
        self._focus_step(-1)

    def focus_after(self, tree: Tree) -> None:
        """Focus the nearest visible tree after ``tree`` (used on collapse)."""
        trees = self.trees
        start = trees.index(tree)
        for index in range(start + 1, len(trees)):
            if trees[index].display:
                trees[index].focus()
                return
        for index in range(start - 1, -1, -1):
            if trees[index].display:
                trees[index].focus()
                return

    def _focus_step(self, direction: int) -> None:
        trees = self.trees
        current = next((i for i, tree in enumerate(trees) if tree.has_focus), None)
        if current is None:
            first = next((tree for tree in trees if tree.display), None)
            if first is not None:
                first.focus()
            return
        index = current + direction
        while 0 <= index < len(trees):
            if trees[index].display:
                trees[index].focus()
                return
            index += direction

    # ------------------------------------------------------------------ #
    # Sections
    # ------------------------------------------------------------------ #

    def section(self, key: str) -> SectionTitle | None:
        """The clickable header for a section (``avds``/``platforms``/``tools``)."""
        return self._sections.get(key)

    def toggle_section(self, key: str) -> None:
        section = self._sections.get(key)
        if section is not None:
            section.toggle()

    def section_collapsed(self, key: str) -> bool:
        section = self._sections.get(key)
        return bool(section and section.collapsed)

    # ------------------------------------------------------------------ #
    # AVDs
    # ------------------------------------------------------------------ #

    async def set_avds(self, avds: list[Avd]) -> None:
        tree = self.avd_tree
        tree.clear()
        tree.root.expand()
        if not avds:
            tree.root.add_leaf(_placeholder("No AVDs defined"))
            tree.root.expand()
            return
        self._marked.clear()
        self._running_avds.clear()
        for avd in avds:
            tree.root.add_leaf(self._avd_label(avd), data=("avd", avd))
        if avds:
            tree.root.children[0].expand()
            tree.move_cursor(tree.root.children[0])
        await asyncio.sleep(0)

    def set_avds_placeholder(self, message: str = "Loading…") -> None:
        tree = self.avd_tree
        tree.clear()
        tree.root.expand()
        tree.root.add_leaf(_placeholder(message))

    # ------------------------------------------------------------------ #
    # Packages
    # ------------------------------------------------------------------ #

    async def set_packages(
        self,
        platform_packages: Iterable[Package],
        tool_packages: Iterable[Package],
    ) -> None:
        """Populate both package trees, preserving expansion state."""
        for tree, packages, label in (
            (self.platforms_tree, list(platform_packages), "No platform packages"),
            (self.tools_tree, list(tool_packages), "No tool packages"),
        ):
            await _populate(tree, packages, label)

    def set_packages_placeholder(self, message: str = "Loading…") -> None:
        for tree in (self.platforms_tree, self.tools_tree):
            tree.clear()
            tree.root.expand()
            tree.root.add_leaf(_placeholder(message))

    def clear_packages(self) -> None:
        for tree in (self.platforms_tree, self.tools_tree):
            tree.clear()
            tree.root.expand()

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #

    def selected(self) -> Selected | None:
        """The :class:`Selected` message for the focused tree's cursor."""
        tree = self._focused_tree()
        if tree is None or tree.cursor_node is None:
            return None
        data = tree.cursor_node.data
        if not isinstance(data, tuple) or len(data) != 2:
            return None
        kind, payload = data
        return Selected(kind, payload)

    def selected_package(self) -> Package | None:
        message = self.selected()
        return message.payload if message and message.kind == "pkg" else None

    def selected_avd(self) -> Avd | None:
        message = self.selected()
        return message.payload if message and message.kind == "avd" else None

    # ------------------------------------------------------------------ #
    # Marking (batch selection)
    # ------------------------------------------------------------------ #

    def toggle_mark(self) -> Package | None:
        """Toggle the mark on the highlighted package; returns it."""
        tree = self._focused_tree()
        if tree is None or tree.cursor_node is None:
            return None
        node = tree.cursor_node
        data = node.data
        if not isinstance(data, tuple) or len(data) != 2 or data[0] != "pkg":
            return None
        pkg: Package = data[1]
        if pkg.path in self._marked:
            self._marked.discard(pkg.path)
        else:
            self._marked.add(pkg.path)
        node.set_label(self._package_label(pkg, marked=pkg.path in self._marked))
        return pkg

    def marked_packages(self) -> list[Package]:
        """Packages marked for a batch operation, in tree order."""
        if not self._marked:
            return []
        found: list[Package] = []
        for tree in (self.platforms_tree, self.tools_tree):
            for node in _walk(tree):
                data = node.data
                if (
                    isinstance(data, tuple)
                    and len(data) == 2
                    and data[0] == "pkg"
                    and data[1].path in self._marked
                ):
                    found.append(data[1])
        return found

    def clear_marks(self) -> None:
        self._marked.clear()
        for tree in (self.platforms_tree, self.tools_tree):
            for node in _walk(tree):
                data = node.data
                if isinstance(data, tuple) and len(data) == 2 and data[0] == "pkg":
                    node.set_label(self._package_label(data[1], marked=False))

    def mark_count(self) -> int:
        return len(self._marked)

    # ------------------------------------------------------------------ #
    # Emulator state
    # ------------------------------------------------------------------ #

    def set_running(self, running: set[str]) -> None:
        """Refresh AVD labels to show which emulators are running."""
        if running == self._running_avds:
            return
        self._running_avds = set(running)
        for node in _walk(self.avd_tree):
            data = node.data
            if isinstance(data, tuple) and len(data) == 2 and data[0] == "avd":
                avd: Avd = data[1]
                node.set_label(self._avd_label(avd, running=avd.name in self._running_avds))

    def _package_label(self, pkg: Package, *, marked: bool = False) -> str:
        """Instance form so marks can be toggled without rebuilding."""
        return _package_label(pkg, marked=marked)

    def _avd_label(self, avd: Avd, *, running: bool = False) -> str:
        return _avd_label(avd, running=running)

    def _focused_tree(self) -> Tree | None:
        focused = self.app.focused if self.app else None
        if isinstance(focused, Tree) and focused in self.trees:
            return focused
        for tree in self.trees:
            if tree.has_focus:
                return tree
        return None


# --------------------------------------------------------------------------- #
# Population helpers
# --------------------------------------------------------------------------- #


async def _populate(tree: Tree, packages: list[Package], empty_label: str) -> None:
    tree.clear()
    tree.root.expand()
    if not packages:
        tree.root.add_leaf(_placeholder(empty_label))
        return

    for group_name, group_packages in _group(packages).items():
        installed = sum(1 for p in group_packages if p.installed)
        total = len(group_packages)
        # TreeNode has no class API, so state is encoded in the label markup.
        css = "installed" if installed else "available"
        count = f"   {installed}/{total}" if installed else ""
        node = tree.root.add(f"[{css}]{group_name}[/{css}]{count}", data=("pkg-group", group_name))

        count = 0
        for pkg in group_packages:
            node.add_leaf(_package_label(pkg), data=("pkg", pkg))
            count += 1
            if count % BATCH == 0:
                # Yield so the UI can paint while a big tree is being built.
                await asyncio.sleep(0)
        await asyncio.sleep(0)


def _package_label(pkg: Package, *, marked: bool = False) -> str:
    """Module-level fallback so tests can call it without a Sidebar instance."""
    icon = INSTALLED_ICON if pkg.installed else AVAILABLE_ICON
    if pkg.updates:
        icon = UPDATE_ICON
    css = "installed" if pkg.installed else ("updatable" if pkg.updates else "available")
    variant = f"  {pkg.variant}" if pkg.variant else ""
    prefix = f"{MARK_ICON} " if marked else ""
    return (
        f"{prefix}[{css}]{icon}[/{css}] {pkg.description or pkg.name}"
        f'[class="detail-value"]  {pkg.version}{variant}[/]'
    )


def _avd_label(avd: Avd, *, running: bool = False) -> str:
    icon = RUNNING_ICON if running else INSTALLED_ICON
    css = "running" if running else "installed"
    return f"[{css}]{icon}[/{css}] {avd.name}"


def _walk(tree: Tree):
    """Depth-first walk over every node of a tree."""
    stack = list(tree.root.children)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def _group(packages: list[Package]) -> dict[str, list[Package]]:
    grouped: dict[str, list[Package]] = {}
    for pkg in packages:
        grouped.setdefault(pkg.group, []).append(pkg)
    for group_packages in grouped.values():
        group_packages.sort(key=_package_sort_key)
    return dict(sorted(grouped.items(), key=_group_sort_key))


def _group_sort_key(item: tuple[str, list[Package]]) -> tuple[int, float, str]:
    """Order API-level groups newest first, everything else alphabetically."""
    label, group_packages = item
    level = next((p.api_level for p in group_packages if p.api_level is not None), None)
    if level is not None:
        return (0, -level, label.lower())
    return (1, 0.0, label.lower())


def _package_sort_key(pkg: Package) -> tuple:
    return (not pkg.installed, pkg.variant.lower(), pkg.version)


def _placeholder(message: str) -> str:
    return f'[class="detail-empty"]{message}[/]'


def iter_nodes(tree: Tree) -> Iterator:
    """Yield every node in a tree (used by tests)."""
    stack = list(tree.root.children)
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)