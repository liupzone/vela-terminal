"""File browser side panel: breadcrumbs, a three-column listing and a toolbar.

Layout follows what a desktop file manager shows: name, permissions and
modification time, with directories listed first and a ``..`` row to go up.
Opening a file reuses :mod:`vela.files`, so text and images open in Vela's own
viewer while everything else goes to the desktop's default application.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import files, fsmodel  # noqa: E402
from . import theme as theme_mod  # noqa: E402

COL_NAME = 0
COL_PERM = 1
COL_MODIFIED = 2
COL_SIZE = 3
COL_ICON = 4
COL_PATH = 5
COL_IS_DIR = 6
COL_TOOLTIP = 7


class FileBrowserPanel(Gtk.Box):
    """A directory listing that can follow the terminal's working directory."""

    def __init__(
        self,
        config,
        theme: theme_mod.Theme,
        on_open: Optional[Callable[[str], bool]] = None,
        notify: Optional[Callable] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.config = config
        self.theme = theme
        self._on_open = on_open
        self._notify = notify or (lambda *_args, **_kwargs: None)
        self.directory = ""
        self.following = True
        self.show_hidden = bool(config.get("filebrowser.show_hidden"))
        self._icon_cache = {}
        self._up_row: Optional[Gtk.TreeIter] = None

        self.get_style_context().add_class("vela-filebrowser")
        # Width belongs to the shared side stack, not to an individual panel.
        self.set_size_request(200, -1)
        self._build()
        self.apply_theme(theme)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build(self) -> None:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title = Gtk.Label(label="文件")
        title.set_xalign(0.0)
        title.get_style_context().add_class("vela-panel-title")
        header.pack_start(title, False, False, 0)

        self._follow_button = self._tool_button(
            "emblem-synchronizing-symbolic",
            "跟随终端目录",
            self.toggle_follow,
        )
        self._hidden_button = self._tool_button(
            "view-conceal-symbolic", "显示隐藏文件", self.toggle_hidden
        )
        self._up_button = self._tool_button(
            "go-up-symbolic", "上级目录", self.go_up
        )
        self._home_button = self._tool_button(
            "go-home-symbolic", "主目录", self.go_home
        )
        self._refresh_button = self._tool_button(
            "view-refresh-symbolic", "刷新", self.refresh
        )
        for button in (
            self._follow_button,
            self._hidden_button,
            self._up_button,
            self._home_button,
            self._refresh_button,
        ):
            header.pack_end(button, False, False, 0)
        self.pack_start(header, False, False, 0)

        self._breadcrumb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        crumb_scroller = Gtk.ScrolledWindow()
        crumb_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        crumb_scroller.set_propagate_natural_height(True)
        crumb_scroller.get_style_context().add_class("vela-crumbs")
        crumb_scroller.add(self._breadcrumb)
        self.pack_start(crumb_scroller, False, False, 0)

        self.store = Gtk.ListStore(
            str,  # name
            str,  # permissions
            str,  # modified
            str,  # size
            GdkPixbuf.Pixbuf,  # icon
            str,  # path
            bool,  # is_dir
            str,  # tooltip
        )
        self.view = Gtk.TreeView(model=self.store)
        self.view.set_headers_visible(True)
        self.view.set_enable_search(False)
        self.view.set_tooltip_column(COL_TOOLTIP)
        self.view.get_style_context().add_class("vela-filelist")
        self.view.connect("row-activated", self._on_row_activated)
        self.view.connect("button-press-event", self._on_button_press)
        self.view.connect("key-press-event", self._on_key_press)

        icon_renderer = Gtk.CellRendererPixbuf()
        name_renderer = Gtk.CellRendererText()
        name_renderer.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        name_column = Gtk.TreeViewColumn("名称")
        name_column.pack_start(icon_renderer, False)
        name_column.add_attribute(icon_renderer, "pixbuf", COL_ICON)
        name_column.pack_start(name_renderer, True)
        name_column.add_attribute(name_renderer, "text", COL_NAME)
        name_column.set_expand(True)
        name_column.set_resizable(True)
        # Keep the minimum small so 权限/修改时间 are never pushed off-screen in
        # a narrow sidebar; the name column ellipsises instead.
        name_column.set_min_width(90)
        name_column.set_sort_column_id(COL_NAME)
        self.view.append_column(name_column)

        perm_renderer = Gtk.CellRendererText()
        perm_renderer.set_property("family", "monospace")
        perm_renderer.set_property("scale", 0.9)
        perm_column = Gtk.TreeViewColumn("权限", perm_renderer, text=COL_PERM)
        perm_column.set_resizable(True)
        perm_column.set_min_width(80)
        perm_column.set_sort_column_id(COL_PERM)
        self.view.append_column(perm_column)

        time_renderer = Gtk.CellRendererText()
        time_renderer.set_property("scale", 0.9)
        time_column = Gtk.TreeViewColumn("修改时间", time_renderer, text=COL_MODIFIED)
        time_column.set_resizable(True)
        time_column.set_min_width(84)
        time_column.set_sort_column_id(COL_MODIFIED)
        self.view.append_column(time_column)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("vela-scroll")
        scroller.add(self.view)
        self.pack_start(scroller, True, True, 0)
        self._scroller = scroller

        self._summary = Gtk.Label(label="")
        self._summary.set_xalign(0.0)
        self._summary.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._summary.get_style_context().add_class("vela-dim")
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_border_width(6)
        footer.pack_start(self._summary, True, True, 0)
        self.pack_start(footer, False, False, 0)

        self._sync_toolbar()

    def _tool_button(self, icon: str, tooltip: str, callback: Callable) -> Gtk.Button:
        button = Gtk.Button()
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
        button.set_tooltip_text(tooltip)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("vela-icon-button")
        button.connect("clicked", lambda *_: callback())
        return button

    # ------------------------------------------------------------------
    # theming
    # ------------------------------------------------------------------
    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        self._icon_cache.clear()
        if self.directory:
            self.refresh()

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def set_directory(self, path: str, force: bool = False) -> bool:
        """Show ``path``; ignored unless following or ``force`` is set."""
        if not force and not self.following:
            return False
        return self._load(path)

    def navigate(self, path: str) -> bool:
        """User-initiated navigation: stops following the terminal."""
        self.following = False
        self._sync_toolbar()
        return self._load(path)

    def go_up(self) -> bool:
        parent = fsmodel.parent_path(self.directory or os.path.expanduser("~"))
        if not parent:
            self._notify("已经是根目录", 3)
            return False
        return self.navigate(parent)

    def go_home(self) -> bool:
        return self.navigate(os.path.expanduser("~"))

    def toggle_follow(self) -> None:
        self.following = not self.following
        self._sync_toolbar()
        if self.following:
            self._notify("文件面板已跟随终端目录", 3)
            directory = self._terminal_directory()
            if directory:
                self._load(directory)
        else:
            self._notify("文件面板已停止跟随终端", 3)

    def toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.config.set("filebrowser.show_hidden", self.show_hidden)
        self._sync_toolbar()
        if self.directory:
            self.refresh()

    def refresh(self) -> bool:
        if not self.directory:
            return False
        return self._load(self.directory)

    def _terminal_directory(self) -> str:
        getter = getattr(self, "terminal_directory", None)
        if getter is None:
            return ""
        try:
            return getter() or ""
        except Exception:  # pragma: no cover - defensive
            return ""

    def _load(self, path: str) -> bool:
        listing = fsmodel.read_directory(
            path,
            show_hidden=self.show_hidden,
            max_entries=int(self.config.get("filebrowser.max_entries")),
            directories_first=bool(
                self.config.get("filebrowser.directories_first")
            ),
        )
        self.directory = listing.path or path
        self._fill(listing)
        return listing.ok

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _fill(self, listing: fsmodel.Listing) -> None:
        self.store.clear()
        self._up_row = None
        if listing.ok:
            parent = fsmodel.parent_path(listing.path)
            if parent:
                icon = self._icon_for_directory(parent)
                self.store.append(
                    [
                        "..",
                        "",
                        "",
                        "",
                        icon,
                        parent,
                        True,
                        f"上级目录：{parent}",
                    ]
                )
                self._up_row = self.store.get_iter_first()
            for entry in listing.entries:
                icon = (
                    self._icon_for_directory(entry.path)
                    if entry.is_dir
                    else self._icon_for_file(entry)
                )
                tooltip = entry.path
                if entry.is_link and entry.link_target:
                    tooltip += f"\n链接到 {entry.link_target}"
                if not entry.readable:
                    tooltip += "\n（没有读取权限）"
                self.store.append(
                    [
                        entry.display_name,
                        entry.permissions,
                        entry.modified,
                        entry.size_text,
                        icon,
                        entry.path,
                        entry.is_dir,
                        tooltip,
                    ]
                )
        self._render_breadcrumbs(listing.path)
        summary = fsmodel.summarize(listing)
        self._summary.set_text(summary)
        self._summary.set_tooltip_text(listing.error or listing.path)
        if listing.error:
            self._summary.get_style_context().add_class("vela-error")
        else:
            self._summary.get_style_context().remove_class("vela-error")

    def _render_breadcrumbs(self, path: str) -> None:
        for child in list(self._breadcrumb.get_children()):
            self._breadcrumb.remove(child)
        if not path:
            self._breadcrumb.show_all()
            return
        crumbs = fsmodel.breadcrumbs(path)
        for index, (label, target) in enumerate(crumbs):
            if index:
                sep = Gtk.Label(label="/")
                sep.get_style_context().add_class("vela-dim")
                self._breadcrumb.pack_start(sep, False, False, 0)
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("vela-crumb")
            button.set_tooltip_text(target)
            # Later crumbs are more useful, so they get the space when the row
            # overflows; the first one keeps the root reachable.
            button.connect("clicked", lambda _b, t=target: self.navigate(t))
            self._breadcrumb.pack_start(button, index == len(crumbs) - 1, False, 0)
        self._breadcrumb.show_all()

    def _sync_toolbar(self) -> None:
        context = self._follow_button.get_style_context()
        if self.following:
            context.add_class("vela-active-toggle")
        else:
            context.remove_class("vela-active-toggle")
        hidden = self._hidden_button.get_style_context()
        if self.show_hidden:
            hidden.add_class("vela-active-toggle")
        else:
            hidden.remove_class("vela-active-toggle")
        self._up_button.set_sensitive(
            bool(self.directory) and bool(fsmodel.parent_path(self.directory))
        )

    # ------------------------------------------------------------------
    # icons
    # ------------------------------------------------------------------
    def _icon_size(self) -> int:
        return int(self.config.get("filebrowser.icon_size"))

    def _load_icon(self, icon, fallback: str) -> Optional[GdkPixbuf.Pixbuf]:
        theme = Gtk.IconTheme.get_default()
        if theme is None:
            return None
        size = self._icon_size()
        try:
            info = theme.lookup_by_gicon(icon, size, Gtk.IconLookupFlags.FORCE_SIZE)
            if info is not None:
                pixbuf = info.load_icon()
                if pixbuf is not None:
                    return pixbuf
        except GLib.Error:
            pass
        try:
            return theme.load_icon(fallback, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            return None

    def _icon_for_directory(self, path: str) -> Optional[GdkPixbuf.Pixbuf]:
        key = "dir"
        if key not in self._icon_cache:
            icon = Gio.ThemedIcon.new("folder")
            self._icon_cache[key] = self._load_icon(icon, "folder")
        return self._icon_cache[key]

    def _icon_for_file(self, entry: fsmodel.Entry) -> Optional[GdkPixbuf.Pixbuf]:
        content_type, _uncertain = Gio.content_type_guess(entry.path, None)
        key = content_type or "unknown"
        if key in self._icon_cache:
            return self._icon_cache[key]
        icon = Gio.content_type_get_icon(content_type) if content_type else None
        if icon is None:
            icon = Gio.ThemedIcon.new("text-x-generic")
        pixbuf = self._load_icon(icon, "text-x-generic")
        self._icon_cache[key] = pixbuf
        return pixbuf

    # ------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------
    def selected_entry(self) -> Optional[fsmodel.Entry]:
        model, tree_iter = self.view.get_selection().get_selected()
        if tree_iter is None:
            return None
        path = model.get_value(tree_iter, COL_PATH)
        if not path:
            return None
        is_dir = bool(model.get_value(tree_iter, COL_IS_DIR))
        return fsmodel.Entry(
            name=os.path.basename(path.rstrip(os.sep)) or path,
            path=path,
            is_dir=is_dir,
        )

    def activate_selected(self) -> bool:
        model, tree_iter = self.view.get_selection().get_selected()
        if tree_iter is None:
            return False
        path = model.get_value(tree_iter, COL_PATH)
        is_dir = bool(model.get_value(tree_iter, COL_IS_DIR))
        if not path:
            return False
        if is_dir:
            return self.navigate(path)
        return self.open_path(path)

    def open_path(self, path: str) -> bool:
        """Open ``path`` the same way the terminal does."""
        if self._on_open is not None:
            return bool(self._on_open(path))
        target = files.classify(path)
        return files.open_with_desktop(target.path)

    def _on_row_activated(self, _view, row, _column) -> None:
        """Open the activated row.

        The ``row-activated`` signal delivers a ``Gtk.TreePath``; PyGObject's
        override of ``Gtk.TreeView.row_activated()`` takes a ``Gtk.TreeIter``.
        Both are accepted so a real double-click and a programmatic call behave
        the same (previously this assumed a TreeIter, so double-clicking a row
        raised inside the handler and did nothing).
        """
        tree_iter = self._as_iter(row)
        if tree_iter is None:
            return
        path = self.store.get_value(tree_iter, COL_PATH)
        if not path:
            return
        if self.store.get_value(tree_iter, COL_IS_DIR):
            self.navigate(path)
        else:
            self.open_path(path)

    def _as_iter(self, row):
        """Normalise a TreePath/TreeIter/None into a TreeIter (or ``None``)."""
        if row is None:
            return None
        if isinstance(row, Gtk.TreeIter):
            return row
        if isinstance(row, Gtk.TreePath):
            try:
                return self.store.get_iter(row)
            except (ValueError, TypeError):
                # A path that no longer exists (the list was refreshed between
                # the click and this handler) must not raise.
                return None
        # A model path string can also arrive from other callers.
        if isinstance(row, str):
            try:
                return self.store.get_iter_from_string(row)
            except (ValueError, TypeError):
                return None
        return None

    def _on_button_press(self, _widget, event) -> bool:
        if event.button != 3:
            return False
        path_info = self.view.get_path_at_pos(int(event.x), int(event.y))
        if path_info is not None:
            self.view.get_selection().select_path(path_info[0])
        self._show_context_menu(event)
        return True

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.activate_selected()
            return True
        if event.keyval == Gdk.KEY_BackSpace:
            self.go_up()
            return True
        if event.keyval == Gdk.KEY_F5:
            self.refresh()
            return True
        return False

    def _show_context_menu(self, event) -> None:
        entry = self.selected_entry()
        menu = Gtk.Menu()

        def add(label: str, callback: Callable, sensitive: bool = True) -> None:
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(sensitive)
            item.connect("activate", lambda *_: callback())
            item.show()
            menu.append(item)

        add("打开", self.activate_selected, entry is not None)
        if entry is not None and not entry.is_dir:
            add("用系统默认程序打开", lambda: self._open_external(entry.path))
        add("在文件管理器中显示", self._reveal_selected, entry is not None)
        add("复制路径", self._copy_selected_path, entry is not None)
        add("复制路径（转义）", self._copy_selected_quoted, entry is not None)
        menu.show_all()
        menu.popup_at_pointer(event)

    def _open_external(self, path: str) -> None:
        if files.open_with_desktop(path):
            self._notify("已用系统默认程序打开", 3)
        else:
            self._notify("找不到可用的系统默认程序", 6, "error")

    def _reveal_selected(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        if files.reveal_in_file_manager(entry.path):
            self._notify("已在文件管理器中显示", 3)
        else:
            self._notify("无法打开文件管理器", 6, "error")

    def _copy_selected_path(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(entry.path, -1)
        clipboard.store()
        self._notify("路径已复制", 3)

    def _copy_selected_quoted(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(files.shell_quote(entry.path), -1)
        clipboard.store()
        self._notify("转义后的路径已复制", 3)
