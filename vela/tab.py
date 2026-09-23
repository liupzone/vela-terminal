"""One tab: a tab label plus a split-pane container of terminals."""

from __future__ import annotations

from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import theme as theme_mod  # noqa: E402
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

    # -- terminals -------------------------------------------------------
    def _make_view(self) -> TerminalView:
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

    def _leaf_for(self, view) -> Optional[Leaf]:
        for leaf in self.container.leaves():
            if leaf.view is view:
                return leaf
        return None

    def bootstrap(self, cwd: Optional[str] = None, argv: Optional[List[str]] = None) -> Leaf:
        leaf = self.container.bootstrap()
        leaf.view.spawn(argv=argv, cwd=cwd)
        self.refresh_title()
        return leaf

    @property
    def active_view(self) -> Optional[TerminalView]:
        leaf = self.container.active
        return leaf.view if leaf is not None else None

    def views(self) -> List[TerminalView]:
        return [leaf.view for leaf in self.container.leaves()]

    # -- state -----------------------------------------------------------
    def refresh_title(self) -> None:
        view = self.active_view
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
    def split(self, orientation: Gtk.Orientation, cwd: Optional[str] = None) -> Optional[Leaf]:
        leaf = self.container.active
        if leaf is None:
            return None
        return self.container.split(leaf, orientation, cwd=cwd)

    def close_active_pane(self) -> bool:
        leaf = self.container.active
        if leaf is None:
            return False
        view = leaf.view
        if not view.exited:
            view.terminate()
        self.container.close(leaf)
        self.refresh_title()
        return self.container.count() > 0

    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        for view in self.views():
            view.apply_theme(theme)

    def apply_appearance(self) -> None:
        for view in self.views():
            view.apply_appearance()

    def terminate_all(self) -> None:
        for view in self.views():
            view.terminate()

    def any_busy(self) -> bool:
        return any(view.has_foreground_process for view in self.views() if not view.exited)
