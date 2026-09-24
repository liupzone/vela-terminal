"""One tab: a tab label plus a split-pane container of terminals."""

from __future__ import annotations

from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import theme as theme_mod  # noqa: E402
from . import layout as layout_mod  # noqa: E402
from . import panels  # noqa: E402
from .pane import Leaf, PaneContainer  # noqa: E402
from .terminal import TerminalView  # noqa: E402


class Tab:
    """Bookkeeping for a single notebook page."""

    def __init__(
        self,
        window,
        config,
        theme: theme_mod.Theme,
        notify: Optional[Callable] = None,
    ) -> None:
        self.window = window
        self.config = config
        self.theme = theme
        self._notify = notify or (lambda *_args, **_kwargs: None)
        self.title_override = ""
        self.pinned = False

        self.label = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.label.set_no_show_all(False)
        # Notebooks with scrollable tabs shrink each tab to its minimum; a
        # minimum width keeps titles readable while still allowing ellipsis.
        self.label.set_size_request(150, -1)
        self.icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic", Gtk.IconSize.MENU)
        self.label.pack_start(self.icon, False, False, 0)
        self.title_label = Gtk.Label(label="终端")
        self.title_label.set_ellipsize(3)
        self.title_label.set_max_width_chars(24)
        self.title_label.set_width_chars(10)
        self.title_label.set_xalign(0.0)
        self.label.pack_start(self.title_label, True, True, 0)
        self.spinner = Gtk.Spinner()
        self.label.pack_start(self.spinner, False, False, 0)
        self.close_button = Gtk.Button()
        self.close_button.set_image(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        )
        self.close_button.set_relief(Gtk.ReliefStyle.NONE)
        self.close_button.set_tooltip_text("关闭标签页")
        self.close_button.get_style_context().add_class("flat")
        self.close_button.connect("clicked", lambda *_: self.window.close_tab(self))
        self.label.pack_start(self.close_button, False, False, 0)
        self.label.show_all()
        self.close_button.set_no_show_all(False)

        self.container = PaneContainer(
            make_view=self._make_view, on_layout_changed=self._on_layout_changed
        )

    # -- panes -----------------------------------------------------------
    def _make_view(self, kind: str = panels.KIND_TERMINAL):
        """Create a pane view: a terminal, or one of the side panels.

        The pane tree is type-agnostic, so this is the single place that decides
        what a new pane contains.
        """
        if kind == panels.KIND_TERMINAL:
            return self._make_terminal()
        if kind == panels.KIND_SYSINFO:
            return panels.PanelView(
                kind, self.window.create_sysinfo_panel(), self.theme
            )
        if kind == panels.KIND_FILEBROWSER:
            return panels.PanelView(
                kind,
                self.window.create_filebrowser_panel(),
                self.theme,
            )
        raise ValueError(f"未知分屏类型：{kind}")

    def _make_terminal(self) -> TerminalView:
        view = TerminalView(self.config, self.theme, notify=self._notify)
        view.connect("title-changed", lambda *_: self.refresh_title())
        view.connect("directory-changed", lambda *_: self._on_layout_changed())
        view.connect("focus-requested", lambda *_: self.container.set_active(self._leaf_for(view)))
        view.connect("child-exited", self._on_child_exited)
        view.connect("open-path", self._on_open_path)
        view.connect("run-action", self._on_run_action)
        # The terminal asks its pane for these when building the context menu.
        view.pane_count = lambda: self.container.count()
        view.pane_zoomed = lambda: self.container.zoomed
        return view

    def _on_open_path(self, _view, path: str, reveal: bool) -> None:
        self.window.open_path(path, reveal=reveal)

    def _on_run_action(self, _view, action: str) -> None:
        self.window.run_action(action)

    def _on_directory_changed(self, view, _path: str) -> None:
        self._on_layout_changed()
        # Only the pane the user is looking at should steer the file panel, and
        # the update is deferred: this runs inside VTE's signal emission, and
        # rebuilding the file list (thousands of GTK calls) from there re-enters
        # GTK and crashes.
        if self.container.active is not None and self.container.active.view is view:
            GLib.idle_add(self.window.follow_terminal_directory)

    def terminals(self) -> List[TerminalView]:
        """Only the terminal panes of this tab, in tree order."""
        return [
            leaf.view
            for leaf in self.container.leaves()
            if panels.is_terminal(getattr(leaf, "kind", panels.KIND_TERMINAL))
        ]

    def panel_panes(self) -> List[Leaf]:
        """Only the leaves holding a side panel."""
        return [
            leaf
            for leaf in self.container.leaves()
            if not panels.is_terminal(getattr(leaf, "kind", panels.KIND_TERMINAL))
        ]

    def active_terminal(self) -> Optional[TerminalView]:
        """The active pane if it is a terminal, else the nearest terminal.

        Terminal-only actions (copy, paste, search, typing) must not silently do
        nothing when a panel pane happens to be active, so they fall back to the
        nearest terminal in the same tab.
        """
        leaf = self.container.active
        if leaf is not None and panels.is_terminal(
            getattr(leaf, "kind", panels.KIND_TERMINAL)
        ):
            return leaf.view
        terminals = self.terminals()
        if not terminals:
            return None
        if leaf is None:
            return terminals[0]
        # Pick the terminal whose pane is closest to the active one.
        active_box = self.container._allocation(leaf)
        if active_box is None:
            return terminals[0]
        cx = active_box[0] + active_box[2] / 2
        cy = active_box[1] + active_box[3] / 2
        best = None
        for terminal in terminals:
            candidate = self._leaf_for(terminal)
            box = self.container._allocation(candidate) if candidate else None
            if box is None:
                continue
            dx = box[0] + box[2] / 2 - cx
            dy = box[1] + box[3] / 2 - cy
            score = dx * dx + dy * dy
            if best is None or score < best[0]:
                best = (score, terminal)
        return best[1] if best else terminals[0]

    def _leaf_for(self, view) -> Optional[Leaf]:
        for leaf in self.container.leaves():
            if leaf.view is view:
                return leaf
        return None

    def bootstrap(self, cwd: Optional[str] = None, argv: Optional[List[str]] = None) -> Leaf:
        leaf = self.container.bootstrap(panels.KIND_TERMINAL)
        leaf.view.spawn(argv=argv, cwd=cwd)
        self.refresh_title()
        return leaf

    @property
    def active_view(self):
        """The active pane's view (a terminal or a panel)."""
        leaf = self.container.active
        return leaf.view if leaf is not None else None

    def views(self) -> List:
        """Every pane view, terminals and panels alike."""
        return [leaf.view for leaf in self.container.leaves()]

    # -- state -----------------------------------------------------------
    def refresh_title(self) -> None:
        """Update the tab label.

        A user-given name wins over the shell's title, and clearing it puts the
        tab back to following the shell.
        """
        # The tab title follows a terminal, never a panel: switching the active
        # split to the file browser must not rename the tab.
        view = self.active_terminal()
        title = self.title_override or (view.title if view else "终端")
        self.title_label.set_text(title)
        self.title_label.set_tooltip_text(title)
        self.window.refresh_tab_titles()

    def set_busy(self, busy: bool) -> None:
        if busy:
            self.spinner.start()
            self.spinner.show()
        else:
            self.spinner.stop()
            self.spinner.hide()

    def set_name(self, name: str) -> None:
        """Give the tab a fixed name; an empty name follows the shell again."""
        self.title_override = (name or "").strip()
        self.refresh_title()

    def name(self) -> str:
        """The tab's current name (its own, or the shell's title)."""
        view = self.active_terminal()
        return self.title_override or (view.title if view else "终端")

    def is_named(self) -> bool:
        return bool(self.title_override)

    # -- layout ----------------------------------------------------------
    def snapshot(self) -> layout_mod.TabSpec:
        """Capture this tab's name and pane tree."""
        return layout_mod.TabSpec(
            title=self.title_override, tree=self._snapshot_node(self.container.root)
        )

    def _snapshot_node(self, node):
        from .pane import Leaf, Split

        if node is None:
            return layout_mod.PaneSpec()
        if isinstance(node, Split):
            return layout_mod.SplitSpec(
                orientation=(
                    "h" if node.orientation == Gtk.Orientation.HORIZONTAL else "v"
                ),
                position=int(node.position or 0),
                first=self._snapshot_node(node.first),
                second=self._snapshot_node(node.second),
            )
        kind = getattr(node, "kind", panels.KIND_TERMINAL)
        cwd = ""
        if panels.is_terminal(kind):
            view = node.view
            cwd = getattr(view, "directory", "") or ""
            if not cwd:
                cwd = str(self.config.get("behavior.working_directory") or "")
        return layout_mod.PaneSpec(kind=kind, cwd=cwd)

    def restore(self, spec: layout_mod.TabSpec) -> None:
        """Rebuild this tab's panes from ``spec``."""
        self.set_name(spec.title)
        self.container.clear()
        self.container.build_from_spec(spec.tree, self._make_view)

    def _on_layout_changed(self) -> None:
        self.window.update_statusbar()
        self.window.refresh_tab_titles()

    def _on_child_exited(self, view, _status: int) -> None:
        """React to a shell exiting: the standard terminal behaviour is to close
        the pane the shell belonged to.

        Nothing is torn down here: this runs inside the terminal's signal
        emission, and removing widgets at that point re-enters GTK (see the note
        on the notebook page handling).  The window is asked to do the work on
        the next idle callback instead.
        """
        self.window.pane_exited(self, view)

    # -- mutations -------------------------------------------------------
    def split(
        self,
        orientation: Gtk.Orientation,
        cwd: Optional[str] = None,
        kind: str = panels.KIND_TERMINAL,
    ) -> Optional[Leaf]:
        leaf = self.container.active
        if leaf is None:
            return None
        return self.container.split(leaf, orientation, cwd=cwd, kind=kind)

    def replace_active_kind(self, kind: str) -> Optional[Leaf]:
        """Turn the active pane into ``kind``, keeping its place in the tree.

        Used by "切换当前分屏为…" so the user does not have to close a pane and
        split again just to change what it shows.
        """
        leaf = self.container.active
        if leaf is None:
            return None
        if getattr(leaf, "kind", panels.KIND_TERMINAL) == kind:
            return leaf
        view = self._make_view(kind)
        old = leaf.view
        leaf.view = view
        leaf.kind = kind
        if hasattr(old, "terminate"):
            old.terminate()
        self.container.replace_view(leaf, view)
        if kind == panels.KIND_TERMINAL and hasattr(view, "spawn"):
            cwd = self.window.current_directory()
            view.spawn(cwd=cwd)
        self.refresh_title()
        self._on_layout_changed()
        return leaf

    def close_active_pane(self) -> bool:
        leaf = self.container.active
        if leaf is None:
            return False
        view = leaf.view
        # A panel pane has no child process to stop.
        if hasattr(view, "terminate") and not getattr(view, "exited", False):
            view.terminate()
        self.container.close(leaf)
        self.refresh_title()
        return self.container.count() > 0

    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        for view in self.views():
            view.apply_theme(theme)

    def apply_appearance(self) -> None:
        # Font, cursor and padding settings only apply to terminals; panel panes
        # have no such options.
        for view in self.terminals():
            view.apply_appearance()

    def terminate_all(self) -> None:
        for view in self.views():
            terminator = getattr(view, "terminate", None)
            if terminator is not None:
                terminator()

    def any_busy(self) -> bool:
        return any(
            getattr(view, "has_foreground_process", False)
            for view in self.terminals()
            if not getattr(view, "exited", False)
        )
