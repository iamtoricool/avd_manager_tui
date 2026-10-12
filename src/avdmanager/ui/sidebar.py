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

INSTALLED_ICON = "●"
AVAILABLE_ICON = "○"
UPDATE_ICON = "↑"


class Selected(Message):
    """Emitted when the user highlights a node in any tree."""

    def __init__(self, kind: str, payload) -> None:
        super().__init__()
        self.kind = kind
        self.payload = payload


class Sidebar(Vertical):
    """Three stacked tree views."""

    DEFAULT_CSS = """
    Sidebar { width: 42; min-width: 30; border-right: solid $primary; }
    Sidebar Tree { height: 1fr; min-height: 4; border: none; padding: 0 1;
                   background: transparent; }
    Sidebar Tree:focus { border: none; }
    Sidebar > .section-title { height: 1; padding: 0 1; color: $text-muted;
                               text-style: bold; background: $panel; }
    """

    def compose(self) -> ComposeResult:
        yield Static("AVDS", classes="section-title")
        yield Tree("", id="avd-tree")

        yield Static("SDK PLATFORMS", classes="section-title")
        yield Tree("", id="platforms-tree")

        yield Static("SDK TOOLS", classes="section-title")
        yield Tree("", id="tools-tree")

    # ------------------------------------------------------------------ #
    # Trees
    # ------------------------------------------------------------------ #

    @property
    def avd_tree(self) -> Tree:
        return self.query_one("#avd-tree", Tree)

    @property
    def platforms_tree(self) -> Tree:
        return self.query_one("#platforms-tree", Tree)

    @property
    def tools_tree(self) -> Tree:
        return self.query_one("#tools-tree", Tree)

    @property
    def trees(self) -> tuple[Tree, Tree, Tree]:
        return (self.avd_tree, self.platforms_tree, self.tools_tree)

    def focus_tree(self, index: int) -> None:
        trees = self.trees
        if 0 <= index < len(trees):
            trees[index].focus()

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
        for avd in avds:
            tree.root.add_leaf(f"{INSTALLED_ICON} {avd.name}", data=("avd", avd))
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
        count = f'  [class="detail-value"]{installed}/{total}[/]' if installed else ""
        node = tree.root.add(f"[{css}]{group_name}[/{css}]{count}", data=("pkg-group", group_name))

        count = 0
        for pkg in group_packages:
            node.add_leaf(_package_label(pkg), data=("pkg", pkg))
            count += 1
            if count % BATCH == 0:
                # Yield so the UI can paint while a big tree is being built.
                await asyncio.sleep(0)
        await asyncio.sleep(0)


def _package_label(pkg: Package) -> str:
    icon = INSTALLED_ICON if pkg.installed else AVAILABLE_ICON
    if pkg.updates:
        icon = UPDATE_ICON
    css = "installed" if pkg.installed else ("updatable" if pkg.updates else "available")
    variant = f"  {pkg.variant}" if pkg.variant else ""
    return (
        f"[{css}]{icon}[/{css}] {pkg.description or pkg.name}"
        f'[class="detail-value"]  {pkg.version}{variant}[/]'
    )


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