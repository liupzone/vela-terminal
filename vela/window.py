"""The main window: header bar, tab strip, split panes, status bar, overlays."""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import keymap, style, theme as theme_mod  # noqa: E402
from . import files as files_mod  # noqa: E402
from . import filebrowser as filebrowser_mod  # noqa: E402
from . import sysinfo as sysinfo_mod  # noqa: E402
from . import viewer as viewer_mod  # noqa: E402
from .palette import CommandPalette  # noqa: E402
from .searchbar import SearchBar  # noqa: E402
from .tab import Tab  # noqa: E402

APP_ACTIONS = {"quit"}


class MainWindow(Gtk.ApplicationWindow):
    """A Vela window; owns tabs, the status bar and the overlays."""

    def __init__(self, app, config, **kwargs) -> None:
        super().__init__(application=app, **kwargs)
        self.app = app
        self.config = config
        self.theme = config.theme()
        self.tabs: List[Tab] = []
        self._closed_tabs: List[Dict[str, object]] = []
        self._status_source = 0
        self._closing = False
        self._css_provider = Gtk.CssProvider()
        self._prefs_dialog = None
        self._viewer_windows: List[Gtk.Window] = []
        # Set while the last tab is being removed because its shell exited, so
        # the notebook's page-removed handler does not queue a replacement tab.
        self._suppress_placeholder = False
        self._is_fullscreen = False
        self.sysinfo_panel: Optional[sysinfo_mod.SysinfoPanel] = None
        self.filebrowser_panel: Optional[filebrowser_mod.FileBrowserPanel] = None
        self.sidebar_visible = False
        self._action_callbacks: Dict[str, Callable] = {}

        self.get_style_context().add_class("vela")
        self.set_default_size(
            int(config.get("window.width")), int(config.get("window.height"))
        )
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("utilities-terminal")

        self._build_headerbar()
        self._build_body()
        self._build_statusbar()
        self._install_css()
        self._create_actions()
        self._connect_signals()

        # NOTE: the window is intentionally *not* shown here.  GtkNotebook only
        # allocates page children once it is itself allocated, so the window must
        # be realized after the first tab exists (see VelaApplication._launch).
        self.apply_window_options()
        if self.sysinfo_panel is not None:
            self.set_sysinfo_visible(
                bool(self.config.get("sysinfo.enabled")), persist=False
            )
        if self.config.get("filebrowser.enabled"):
            self.set_filebrowser_visible(True, persist=False)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_headerbar(self) -> None:
        self.headerbar = Gtk.HeaderBar()
        self.headerbar.get_style_context().add_class("vela-headerbar")
        self.headerbar.set_show_close_button(True)
        self.headerbar.set_title("Vela Terminal")
        self.headerbar.set_subtitle("")

        self.new_tab_button = self._header_button(
            "tab-new-symbolic", "新建标签页 (Ctrl+Shift+T)", lambda *_: self.new_tab()
        )
        self.headerbar.pack_start(self.new_tab_button)

        self.split_button = self._header_button(
            "view-dual-symbolic",
            "左右分屏 (Ctrl+Shift+O)",
            lambda *_: self.split(Gtk.Orientation.HORIZONTAL),
        )
        self.headerbar.pack_start(self.split_button)

        self.search_button = self._header_button(
            "edit-find-symbolic", "搜索 (Ctrl+Shift+F)", lambda *_: self.toggle_search()
        )
        self.headerbar.pack_end(self.search_button)

        self.palette_button = self._header_button(
            "system-run-symbolic",
            "命令面板 (Ctrl+Shift+P)",
            lambda *_: self.toggle_palette(),
        )
        self.headerbar.pack_end(self.palette_button)

        self.sysinfo_button = self._header_button(
            "utilities-system-monitor-symbolic",
            "系统性能 (Ctrl+Shift+M)",
            lambda *_: self.toggle_sysinfo(),
        )
        self.headerbar.pack_end(self.sysinfo_button)

        self.filebrowser_button = self._header_button(
            "folder-symbolic",
            "文件面板 (Ctrl+Shift+B)",
            lambda *_: self.toggle_filebrowser(),
        )
        self.headerbar.pack_end(self.filebrowser_button)

        self.menu_button = Gtk.MenuButton()
        self.menu_button.set_image(
            Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.MENU)
        )
        self.menu_button.set_tooltip_text("主菜单")
        self.menu_button.set_menu_model(self._build_menu_model())
        self.headerbar.pack_end(self.menu_button)

        self.set_titlebar(self.headerbar)

    def _header_button(self, icon: str, tooltip: str, callback: Callable) -> Gtk.Button:
        button = Gtk.Button()
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
        button.set_tooltip_text(tooltip)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.connect("clicked", callback)
        return button

    # ------------------------------------------------------------------
    # sysinfo panel
    # ------------------------------------------------------------------
    def _create_sysinfo_panel(self) -> sysinfo_mod.SysinfoPanel:
        panel = sysinfo_mod.SysinfoPanel(
            self.config,
            self.theme,
            pane_provider=self._pane_process_list,
            notify=self.show_status,
        )
        panel.on_snapshot = self._on_sysinfo_snapshot
        # Width is owned by the side stack (both panels share one column), so the
        # panel itself only asks for a sensible minimum.
        panel.set_size_request(200, -1)
        self.sysinfo_panel = panel
        return panel

    def _on_sysinfo_snapshot(self, snapshot) -> None:
        """Mirror the panel's own reading into the status bar."""
        if not self.config.get("sysinfo.show_in_statusbar"):
            self.status_sysinfo.set_text("")
            return
        self.status_sysinfo.set_text(
            f"CPU {snapshot.cpu_percent:.0f}% · 内存 {snapshot.memory.percent:.0f}%"
        )

    def _pane_process_list(self) -> List[Tuple[str, int]]:
        tab = self.active_tab
        if tab is None:
            return []
        rows: List[Tuple[str, int]] = []
        for view in tab.views():
            if view.child_pid and not view.exited:
                rows.append((view.title, view.child_pid))
        return rows

    def set_sysinfo_visible(self, visible: bool, persist: bool = True) -> None:
        if visible and self.sysinfo_panel is None:
            self._create_sysinfo_panel()
        panel = self.sysinfo_panel
        if panel is None:
            return
        if visible:
            self._mount_panel(panel)
            panel.show_all()
            panel.start()
        else:
            panel.stop()
            panel.hide()
            self._unmount_panel(panel)
        if hasattr(self, "sysinfo_button"):
            # Visual state for the header button; Gtk.Button has no "active".
            context = self.sysinfo_button.get_style_context()
            if visible:
                context.add_class("vela-active-toggle")
            else:
                context.remove_class("vela-active-toggle")
        self.config.set("sysinfo.enabled", visible)
        if persist:
            self._save_config()
        self._sync_sidebar()
        self.update_statusbar()

    # ------------------------------------------------------------------
    # file browser panel
    # ------------------------------------------------------------------
    def _create_filebrowser_panel(self) -> "filebrowser_mod.FileBrowserPanel":
        panel = filebrowser_mod.FileBrowserPanel(
            self.config,
            self.theme,
            on_open=self.open_path,
            notify=self.show_status,
        )
        # The panel asks for the terminal's directory whenever it needs it, so
        # following stays correct even after switching tabs.
        panel.terminal_directory = self._terminal_directory
        panel.following = bool(self.config.get("filebrowser.follow_terminal"))
        panel.set_size_request(200, -1)
        self.filebrowser_panel = panel
        return panel

    def _terminal_directory(self) -> str:
        view = self.active_view
        if view is None:
            return ""
        return view.directory or os.path.expanduser("~")

    def set_filebrowser_visible(self, visible: bool, persist: bool = True) -> None:
        if visible and self.filebrowser_panel is None:
            self._create_filebrowser_panel()
        panel = self.filebrowser_panel
        if panel is None:
            return
        if visible:
            self._mount_panel(panel)
            panel.show_all()
            panel._sync_toolbar()
            directory = self._terminal_directory()
            if directory:
                panel.set_directory(directory, force=not panel.following)
            if not panel.directory:
                panel.navigate(os.path.expanduser("~"))
        else:
            panel.hide()
            self._unmount_panel(panel)
        if hasattr(self, "filebrowser_button"):
            context = self.filebrowser_button.get_style_context()
            if visible:
                context.add_class("vela-active-toggle")
            else:
                context.remove_class("vela-active-toggle")
        self.config.set("filebrowser.enabled", visible)
        if persist:
            self._save_config()
        self._sync_sidebar()
        self.update_statusbar()

    def toggle_filebrowser(self) -> None:
        visible = self._filebrowser_visible()
        self.set_filebrowser_visible(not visible)
        self.show_status("文件面板已" + ("关闭" if visible else "打开"), 2)

    def _filebrowser_visible(self) -> bool:
        return bool(
            self.filebrowser_panel is not None and self.filebrowser_panel.get_visible()
        )

    def _mount_panel(self, panel: Gtk.Widget) -> None:
        """Attach a side panel to the vertical stack, once.

        The file browser goes on top and the system monitor below it, matching
        how the two are used together: the file list is scanned from the top
        while the live counters can be left running underneath.
        """
        if panel.get_parent() is not None:
            return
        if panel is self.filebrowser_panel:
            if self.side_paned.get_child1() is None:
                self.side_paned.pack1(panel, True, False)
            else:
                # The monitor was mounted first; keep the browser on top.
                self.side_paned.pack1(panel, True, False)
                monitor = self.side_paned.get_child2()
                if monitor is not None:
                    self.side_paned.remove(monitor)
                    self.side_paned.pack2(monitor, True, False)
        else:
            if self.side_paned.get_child2() is None:
                self.side_paned.pack2(panel, True, False)
            else:
                self.side_paned.pack2(panel, True, False)

    def _unmount_panel(self, panel: Gtk.Widget) -> None:
        """Detach a panel and give its space back to the one that remains."""
        parent = panel.get_parent()
        if parent is None:
            return
        parent.remove(panel)
        # GtkPaned hands the whole area to the surviving child once one slot is
        # empty, so no divider arithmetic is needed here.

    def _panels_visible(self) -> List[Gtk.Widget]:
        return [
            panel
            for panel in (self.filebrowser_panel, self.sysinfo_panel)
            if panel is not None and panel.get_visible()
        ]

    def _sync_sidebar(self) -> None:
        """Show the side stack only when at least one panel is visible."""
        visible = self._panels_visible()
        self.sidebar_visible = bool(visible)
        if not visible:
            self.side_paned.set_visible(False)
            return
        self.side_paned.set_visible(True)
        # apply_window_options() runs before any panel is mounted, so the width
        # has to be (re)applied here as well.
        self.side_paned.set_size_request(
            int(self.config.get("window.sidebar_width")), -1
        )
        self.side_paned.show_all()
        # With both panels open, split the height evenly the first time so
        # neither starts at a useless sliver; afterwards the user's drag wins.
        if len(visible) == 2 and not getattr(self, "_side_split_placed", False):
            GLib.idle_add(self._place_side_divider)

    def _place_side_divider(self) -> bool:
        height = self.side_paned.get_allocated_height()
        if height > 120:
            self.side_paned.set_position(height // 2)
            self._side_split_placed = True
        return False

    def toggle_sysinfo(self) -> None:
        panel = self.sysinfo_panel
        visible = bool(panel is not None and panel.get_visible())
        self.set_sysinfo_visible(not visible)
        self.show_status("系统性能面板已" + ("关闭" if visible else "打开"), 2)

    def _sysinfo_visible(self) -> bool:
        return bool(self.sysinfo_panel is not None and self.sysinfo_panel.get_visible())

    # ------------------------------------------------------------------
    # opening files
    # ------------------------------------------------------------------
    def open_path(self, raw: Optional[str] = None, reveal: bool = False) -> bool:
        """Open ``raw`` (or the path the user is pointing at) appropriately."""
        path = raw if raw is not None else self._current_path()
        if not path:
            self.show_status(
                "光标附近没有路径；选中一个路径再按 Ctrl+Enter，或用 Ctrl+点击",
                6,
                "warning",
            )
            return False
        target = files_mod.classify(path)
        if not target.exists:
            self.show_status(f"路径不存在：{_shorten_path(target.path)}", 6, "error")
            return False
        if reveal:
            if files_mod.reveal_in_file_manager(target.path):
                self.show_status("已在文件管理器中显示", 3)
                return True
            self.show_status("无法打开文件管理器", 6, "error")
            return False
        if target.kind == files_mod.KIND_DIRECTORY:
            if files_mod.open_with_desktop(target.path):
                self.show_status("已用文件管理器打开目录", 3)
                return True
            self.show_status("无法打开文件管理器", 6, "error")
            return False
        if target.openable_internally and self.config.get("files.open_internally"):
            return self.open_in_viewer(target)
        if target.reason:
            self.show_status(target.reason, 5, "warning")
        if files_mod.open_with_desktop(target.path):
            self.show_status("已用系统默认程序打开", 3)
            return True
        self.show_status("找不到可用的系统默认程序", 6, "error")
        return False

    def _current_path(self) -> str:
        view = self.active_view
        if view is None:
            return ""
        try:
            return view.path_under_cursor()
        except Exception as error:  # pragma: no cover - defensive
            self.show_status(f"无法识别路径：{error}", 6, "error")
            return ""

    def open_selection(self) -> bool:
        view = self.active_view
        if view is None:
            return False
        if not view.terminal.get_has_selection():
            self.show_status("请先选中要打开的路径", 4, "warning")
            return False
        text = view.get_selected_text().strip()
        if not text:
            self.show_status("请先选中要打开的路径", 4, "warning")
            return False
        # Only existing paths inside the selection count; otherwise a selection
        # that happens to contain no path would fall back to "open everything".
        candidates = files_mod.candidate_paths(text)
        if not candidates and files_mod.classify(text).exists:
            candidates = [text]
        for candidate in candidates:
            if files_mod.classify(candidate).exists:
                return self.open_path(candidate)
        self.show_status("选区中没有可打开的路径", 5, "warning")
        return False

    def reveal_path(self) -> bool:
        return self.open_path(self._current_path(), reveal=True)

    def open_in_viewer(self, target: files_mod.Target) -> bool:
        try:
            window = viewer_mod.FileViewerWindow(
                self, target.path, target, self.theme, notify=self.show_status
            )
        except (OSError, ValueError, GLib.Error) as error:
            self.show_status(f"无法打开 {target.path}：{error}", 8, "error")
            return False
        window.show_all()
        window.connect("destroy", self._on_viewer_destroyed)
        self._viewer_windows.append(window)
        self.show_status(f"已打开 {os.path.basename(target.path)}", 3)
        return True

    def _on_viewer_destroyed(self, window: Gtk.Window) -> None:
        if window in self._viewer_windows:
            self._viewer_windows.remove(window)

    def close_viewers(self) -> None:
        for window in list(self._viewer_windows):
            window.destroy()
        self._viewer_windows.clear()

    def _build_menu_model(self) -> Gio.Menu:
        menu = Gio.Menu()
        file_section = Gio.Menu()
        file_section.append("新建标签页", "win.new-tab")
        file_section.append("新建窗口", "app.new-window")
        file_section.append("重新打开已关闭的标签页", "win.reopen-tab")
        menu.append_section(None, file_section)

        split_section = Gio.Menu()
        split_section.append("垂直分屏", "win.split-vertical")
        split_section.append("水平分屏", "win.split-horizontal")
        split_section.append("关闭当前分屏", "win.close-pane")
        split_section.append("最大化当前分屏", "win.zoom-pane")
        menu.append_section(None, split_section)

        edit_section = Gio.Menu()
        edit_section.append("搜索", "win.find")
        edit_section.append("复制", "win.copy")
        edit_section.append("粘贴", "win.paste")
        edit_section.append("粘贴为转义文本", "win.paste-escaped")
        edit_section.append("清屏", "win.clear")
        edit_section.append("重置终端", "win.reset")
        edit_section.append("打开路径 / 文件", "win.open-path")
        edit_section.append("打开选区", "win.open-selection")
        edit_section.append("在文件管理器中显示", "win.reveal-path")
        menu.append_section(None, edit_section)

        theme_section = Gio.Menu()
        themes_menu = Gio.Menu()
        for name in theme_mod.all_names():
            item = Gio.MenuItem.new(theme_mod.label(name), None)
            item.set_action_and_target_value(
                "win.set-theme", GLib.Variant.new_string(name)
            )
            themes_menu.append_item(item)
        theme_section.append_submenu("配色主题", themes_menu)
        theme_section.append("命令面板", "win.command-palette")
        theme_section.append("首选项", "win.open-preferences")
        menu.append_section(None, theme_section)

        view_section = Gio.Menu()
        view_section.append("显示状态栏", "win.toggle-statusbar")
        view_section.append("系统性能面板", "win.toggle-sysinfo")
        view_section.append("文件面板", "win.toggle-filebrowser")
        view_section.append("全屏", "win.toggle-fullscreen")
        view_section.append("放大字号", "win.font-increase")
        view_section.append("缩小字号", "win.font-decrease")
        view_section.append("重置字号", "win.font-reset")
        menu.append_section(None, view_section)

        about_section = Gio.Menu()
        about_section.append("关于 Vela", "app.about")
        about_section.append("退出", "app.quit")
        menu.append_section(None, about_section)
        return menu

    def _build_body(self) -> None:
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(False)
        self.notebook.set_group_name("vela-tabs")
        self.notebook.get_style_context().add_class("vela-notebook")
        self.notebook.set_tab_pos(self._tab_position())
        self.notebook.popup_enable()
        self.notebook.connect("switch-page", self._on_switch_page)
        self.notebook.connect("page-removed", self._on_page_removed)
        self.notebook.connect("page-reordered", lambda *_: self._persist_tab_order())

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.content.pack_start(self.notebook, True, True, 0)

        self.overlay = Gtk.Overlay()
        self.overlay.add(self.content)

        self.search_bar = SearchBar(self._on_search, on_close=self._on_search_closed)
        self.search_revealer = Gtk.Revealer()
        # SLIDE_DOWN never gets past its initial 1px allocation on this GTK
        # build, so the revealer uses a crossfade instead.
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.search_revealer.set_transition_duration(120)
        self.search_revealer.set_halign(Gtk.Align.END)
        self.search_revealer.set_valign(Gtk.Align.START)
        self.search_revealer.set_margin_top(10)
        self.search_revealer.set_margin_end(14)
        self.search_revealer.add(self.search_bar)
        self.overlay.add_overlay(self.search_revealer)

        self.palette = CommandPalette(self.run_action)
        self.palette.set_size_request(560, 420)
        self.palette.set_halign(Gtk.Align.CENTER)
        self.palette.set_valign(Gtk.Align.START)
        self.palette.set_margin_top(70)
        self.overlay.add_overlay(self.palette)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Side panels live beside the terminal area so toggling them never
        # touches the notebook itself (no page reparenting, no shell restarts).
        self.side_container = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=0
        )
        self.side_container.pack_start(self.overlay, True, True, 0)

        # The panels are stacked vertically inside a Gtk.Paned so the divider is
        # draggable.  The paned is created here, at startup: creating one later
        # and packing children into it leaves it at a 1px allocation on this GTK
        # version.  It stays hidden until a panel is shown.
        self.side_paned = Gtk.Paned.new(Gtk.Orientation.VERTICAL)
        self.side_paned.get_style_context().add_class("vela-paned")
        self.side_paned.set_wide_handle(False)
        self.side_paned.set_visible(False)
        self.side_container.pack_end(self.side_paned, False, False, 0)
        self.body.pack_start(self.side_container, True, True, 0)
        self.add(self.body)

        if self.config.get("sysinfo.enabled"):
            self._create_sysinfo_panel()

    def _build_statusbar(self) -> None:
        self.statusbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.statusbar.get_style_context().add_class("vela-statusbar")

        self.status_dir = Gtk.Label(label="")
        self.status_dir.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.status_dir.set_xalign(0.0)
        self.status_dir.set_max_width_chars(48)
        self.statusbar.pack_start(self.status_dir, False, False, 0)

        self.status_message = Gtk.Label(label="")
        self.status_message.get_style_context().add_class("vela-status-accent")
        self.status_message.set_ellipsize(Pango.EllipsizeMode.END)
        self.statusbar.pack_start(self.status_message, True, True, 0)

        self.status_panes = Gtk.Label(label="")
        self.statusbar.pack_end(self.status_panes, False, False, 0)
        self.status_theme = Gtk.Label(label="")
        self.statusbar.pack_end(self.status_theme, False, False, 0)
        self.status_sysinfo = Gtk.Label(label="")
        self.statusbar.pack_end(self.status_sysinfo, False, False, 0)
        self.status_cursor = Gtk.Label(label="")
        self.statusbar.pack_end(self.status_cursor, False, False, 0)

        self.body.pack_start(self.statusbar, False, False, 0)

    def _install_css(self) -> None:
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._refresh_css()

    def _refresh_css(self) -> None:
        try:
            self._css_provider.load_from_data(style.build_css(self.theme).encode("utf-8"))
        except GLib.Error as error:  # pragma: no cover - CSS is generated
            self.show_status(f"样式表错误：{error.message}", 6, "error")

    def _connect_signals(self) -> None:
        self.connect("delete-event", self._on_delete_event)
        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-in-event", lambda *_: self.update_statusbar())

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _create_actions(self) -> None:
        specs: List[Tuple[str, Callable]] = [
            ("new-tab", lambda *_: self.new_tab()),
            ("close-tab", lambda *_: self.close_tab()),
            ("reopen-tab", lambda *_: self.reopen_tab()),
            ("next-tab", lambda *_: self.switch_tab(1)),
            ("prev-tab", lambda *_: self.switch_tab(-1)),
            ("move-tab-left", lambda *_: self.move_tab(-1)),
            ("move-tab-right", lambda *_: self.move_tab(1)),
            ("split-vertical", lambda *_: self.split(Gtk.Orientation.HORIZONTAL)),
            ("split-horizontal", lambda *_: self.split(Gtk.Orientation.VERTICAL)),
            ("close-pane", lambda *_: self.close_pane()),
            ("focus-left", lambda *_: self.focus_direction("left")),
            ("focus-right", lambda *_: self.focus_direction("right")),
            ("focus-up", lambda *_: self.focus_direction("up")),
            ("focus-down", lambda *_: self.focus_direction("down")),
            ("zoom-pane", lambda *_: self.zoom_pane()),
            ("find", lambda *_: self.toggle_search()),
            ("find-next", lambda *_: self.search_bar.find_next()),
            ("find-previous", lambda *_: self.search_bar.find_previous()),
            ("command-palette", lambda *_: self.toggle_palette()),
            ("font-increase", lambda *_: self.change_font_size(1)),
            ("font-decrease", lambda *_: self.change_font_size(-1)),
            ("font-reset", lambda *_: self.reset_font_size()),
            ("copy", lambda *_: self.copy()),
            ("paste", lambda *_: self.paste()),
            ("paste-escaped", lambda *_: self.paste_escaped()),
            ("select-all", lambda *_: self.select_all()),
            ("clear", lambda *_: self.clear()),
            ("reset", lambda *_: self.reset()),
            ("toggle-fullscreen", lambda *_: self.toggle_fullscreen()),
            ("toggle-statusbar", lambda *_: self.toggle_statusbar()),
            ("toggle-sysinfo", lambda *_: self.toggle_sysinfo()),
            ("toggle-filebrowser", lambda *_: self.toggle_filebrowser()),
            ("open-path", lambda *_: self.open_path()),
            ("open-selection", lambda *_: self.open_selection()),
            ("reveal-path", lambda *_: self.reveal_path()),
            ("open-preferences", lambda *_: self.open_preferences()),
            ("new-window", lambda *_: self.app.new_window()),
        ]
        for name, callback in specs:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
            self._action_callbacks[name] = callback

        theme_action = Gio.SimpleAction.new("set-theme", GLib.VariantType.new("s"))
        theme_action.connect(
            "activate", lambda _a, param: self.set_theme(param.get_string())
        )
        self.add_action(theme_action)

        simple_app_actions = [
            ("quit", lambda *_: self.app.quit_app()),
            ("about", lambda *_: self.show_about()),
        ]
        for name, callback in simple_app_actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.app.add_action(action)

        bindings = self.config.keybindings()
        window_bindings = {
            key: value for key, value in bindings.items() if key not in APP_ACTIONS
        }
        app_bindings = {
            key: value for key, value in bindings.items() if key in APP_ACTIONS
        }
        rejected = keymap.apply(self.app, window_bindings, "win")
        rejected += keymap.apply(self.app, app_bindings, "app")
        if rejected:
            self.show_status(
                "以下快捷键无法识别：" + "、".join(sorted(set(rejected))), 8, "warning"
            )
        conflicts = keymap.find_conflicts(bindings)
        if conflicts:
            first = conflicts[0]
            self.show_status(
                f"快捷键冲突：{keymap.display(first[0])} 同时绑定了 "
                f"{first[1]} 与 {first[2]}",
                8,
                "warning",
            )

    def run_action(self, action: str) -> None:
        if action.startswith("theme:"):
            self.set_theme(action.split(":", 1)[1])
            return
        target = action.replace("_", "-")
        callback = self._action_callbacks.get(target)
        if callback is not None:
            callback()
            return
        self.app.activate_action(target, None)

    # ------------------------------------------------------------------
    # tabs
    # ------------------------------------------------------------------
    def new_tab(
        self,
        cwd: Optional[str] = None,
        argv: Optional[List[str]] = None,
        focus: bool = True,
    ) -> Tab:
        tab = Tab(self, self.config, self.theme, notify=self.show_status)
        tab.bootstrap(cwd=cwd, argv=argv)
        page = self.notebook.append_page(tab.container, tab.label)
        self.notebook.set_tab_reorderable(tab.container, True)
        self.notebook.set_tab_detachable(tab.container, True)
        # The window was already shown when the first tab is added, so the new
        # page has to be revealed explicitly; otherwise GtkNotebook reports no
        # current page and the tab strip stays empty.
        tab.container.show_all()
        tab.label.show_all()
        self.tabs.append(tab)
        if focus:
            self.notebook.set_current_page(page)
            tab.container.focus_active()
        self.notebook.show_all()
        self.update_tab_visibility()
        self.update_statusbar()
        self.refresh_window_title()
        return tab

    def tab_for_widget(self, widget) -> Optional[Tab]:
        for tab in self.tabs:
            if tab.container is widget:
                return tab
        return None

    @property
    def active_tab(self) -> Optional[Tab]:
        index = self.notebook.get_current_page()
        if 0 <= index < len(self.tabs):
            return self.tabs[index]
        return self.tabs[0] if self.tabs else None

    @property
    def active_view(self):
        tab = self.active_tab
        return tab.active_view if tab else None

    def close_tab(
        self,
        tab: Optional[Tab] = None,
        force: bool = False,
        from_shell_exit: bool = False,
    ) -> None:
        tab = tab or self.active_tab
        if tab is None:
            return
        if not force and self.config.get("behavior.confirm_close") and tab.any_busy():
            if not self._confirm_close_tab(tab):
                return
        self._remember_closed_tab(tab)
        tab.terminate_all()
        if tab in self.tabs:
            self.tabs.remove(tab)
        quitting = False
        if not self.tabs and from_shell_exit and self.config.get(
            "behavior.close_window_on_last_exit"
        ):
            # Set before remove_page() so the page-removed handler stays quiet.
            self._suppress_placeholder = True
            quitting = True
        page = self.notebook.page_num(tab.container)
        if page >= 0:
            self.notebook.remove_page(page)
        if not self.tabs:
            if self._closing:
                return
            if quitting:
                # The user typed "exit" in the only remaining shell: quit.
                GLib.idle_add(self.window_exited)
            else:
                GLib.idle_add(self._spawn_placeholder_tab)
        self.update_tab_visibility()
        self.update_statusbar()
        self.refresh_window_title()

    def _spawn_placeholder_tab(self) -> bool:
        """Create a fresh tab once the current GTK signal emission has finished.

        Adding a notebook page from inside ``page-removed`` re-enters GTK while
        the notebook is still mutating its child list, which crashes VTE.
        """
        if not self.tabs and not self._closing:
            self.new_tab()
        return False

    def _remember_closed_tab(self, tab: Tab) -> None:
        view = tab.active_view
        if view is None:
            return
        self._closed_tabs.append(
            {
                "title": view.title,
                "cwd": view.directory,
                "argv": view.default_argv(),
            }
        )
        del self._closed_tabs[:-10]

    def reopen_tab(self) -> None:
        if not self._closed_tabs:
            self.show_status("没有可重新打开的标签页", 3)
            return
        spec = self._closed_tabs.pop()
        cwd = spec.get("cwd") or None
        if cwd and not os.path.isdir(str(cwd)):
            cwd = None
        tab = self.new_tab(cwd=cwd, argv=spec.get("argv") or None)
        tab.title_override = str(spec.get("title") or "")
        tab.refresh_title()

    def switch_tab(self, step: int) -> None:
        if len(self.tabs) < 2:
            return
        index = (self.notebook.get_current_page() + step) % len(self.tabs)
        self.notebook.set_current_page(index)
        tab = self.active_tab
        if tab:
            tab.container.focus_active()

    def move_tab(self, step: int) -> None:
        index = self.notebook.get_current_page()
        target = index + step
        if not 0 <= target < len(self.tabs):
            return
        self.notebook.reorder_child(self.tabs[index].container, target)
        self.tabs.insert(target, self.tabs.pop(index))
        self.notebook.set_current_page(target)

    def _persist_tab_order(self) -> None:
        ordered: List[Tab] = []
        for index in range(self.notebook.get_n_pages()):
            tab = self.tab_for_widget(self.notebook.get_nth_page(index))
            if tab is not None:
                ordered.append(tab)
        if len(ordered) == len(self.tabs):
            self.tabs = ordered

    def _on_page_removed(self, _notebook, child, _page: int) -> None:
        tab = self.tab_for_widget(child)
        if tab is not None and tab in self.tabs:
            self._remember_closed_tab(tab)
            tab.terminate_all()
            self.tabs.remove(tab)
            if (
                not self.tabs
                and not self._closing
                and not self._suppress_placeholder
            ):
                GLib.idle_add(self._spawn_placeholder_tab)
            self.update_tab_visibility()
            self.update_statusbar()
            self.refresh_window_title()

    def _on_switch_page(self, _notebook, _child, _page: int) -> None:
        tab = self.active_tab
        if tab is not None:
            tab.container.focus_active()
        self.update_statusbar()
        self.refresh_window_title()
        self.follow_terminal_directory()

    def follow_terminal_directory(self) -> None:
        """Point the file panel at the active pane's directory, if it follows."""
        panel = self.filebrowser_panel
        if panel is None or not panel.get_visible() or not panel.following:
            return
        directory = self._terminal_directory()
        if not directory:
            return
        if os.path.abspath(directory) != os.path.abspath(panel.directory or ""):
            panel.set_directory(directory)

    def update_tab_visibility(self) -> None:
        mode = self.config.get("window.show_tabbar")
        count = self.notebook.get_n_pages()
        show = mode == "always" or (mode == "multiple" and count > 1)
        self.notebook.set_show_tabs(show)

    def refresh_tab_titles(self) -> None:
        for tab in self.tabs:
            view = tab.active_view
            if view is None:
                continue
            if not tab.title_override:
                tab.title_label.set_text(view.title)
        self.refresh_window_title()

    def refresh_window_title(self) -> None:
        tab = self.active_tab
        if tab is None:
            self.headerbar.set_title("Vela Terminal")
            self.set_title("Vela Terminal")
            return
        view = tab.active_view
        title = tab.title_override or (view.title if view else "终端")
        self.headerbar.set_title(title)
        directory = view.directory if view else ""
        subtitle = _shorten_path(directory) if directory else ""
        panes = tab.container.count()
        if panes > 1:
            subtitle = f"{subtitle} · {panes} 个分屏" if subtitle else f"{panes} 个分屏"
        self.headerbar.set_subtitle(subtitle)
        self.set_title(f"{title} — Vela Terminal")

    # ------------------------------------------------------------------
    # panes
    # ------------------------------------------------------------------
    def split(self, orientation: Gtk.Orientation) -> None:
        tab = self.active_tab
        if tab is None:
            return
        view = tab.active_view
        cwd = view.directory if view and self.config.get(
            "window.restore_working_directory"
        ) else None
        if cwd and not os.path.isdir(cwd):
            cwd = None
        leaf = tab.split(orientation, cwd=cwd)
        if leaf is None:
            return
        self.update_statusbar()
        self.refresh_window_title()
        self.show_status("已分屏", 2)

    def close_pane(self) -> None:
        tab = self.active_tab
        if tab is None:
            return
        if tab.container.count() <= 1:
            self.close_tab(tab)
            return
        if not tab.close_active_pane():
            self.close_tab(tab)
            return
        self.update_statusbar()
        self.refresh_window_title()

    def focus_direction(self, direction: str) -> None:
        tab = self.active_tab
        if tab is None:
            return
        if not tab.container.focus_direction(direction):
            return
        self.update_statusbar()

    def zoom_pane(self) -> None:
        tab = self.active_tab
        if tab is None:
            return
        zoomed = tab.container.toggle_zoom()
        self.show_status("分屏已最大化" if zoomed else "已还原分屏布局", 2)
        self.refresh_window_title()

    # ------------------------------------------------------------------
    # editing
    # ------------------------------------------------------------------
    def _with_view(self, callback: Callable) -> bool:
        view = self.active_view
        if view is None:
            return False
        callback(view)
        return True

    def copy(self) -> None:
        if not self._with_view(lambda view: view.copy()):
            return
        self.show_status("已复制", 2)

    def paste(self) -> None:
        self._with_view(lambda view: view.paste())

    def paste_escaped(self) -> None:
        self._with_view(lambda view: view.paste_escaped())
        self.show_status("已粘贴为转义文本", 2)

    def select_all(self) -> None:
        self._with_view(lambda view: view.select_all())

    def clear(self) -> None:
        self._with_view(lambda view: view.clear_screen())

    def reset(self) -> None:
        if not self._with_view(lambda view: view.reset_terminal()):
            return
        self.show_status("终端已重置", 2)

    # ------------------------------------------------------------------
    # search / palette
    # ------------------------------------------------------------------
    def toggle_search(self) -> None:
        if self.search_revealer.get_reveal_child():
            self.search_bar.close()
        else:
            self.search_revealer.set_reveal_child(True)
            selected = ""
            view = self.active_view
            if view is not None and view.terminal.get_has_selection():
                selected = view.get_selected_text().strip()
            self.search_bar.open(selected if len(selected) < 120 else "")

    def _on_search(self, pattern: str, regex: bool, forward: bool) -> bool:
        view = self.active_view
        if view is None:
            return False
        return view.search(pattern, regex=regex, forward=forward)

    def _on_search_closed(self) -> None:
        self.search_revealer.set_reveal_child(False)
        view = self.active_view
        if view is not None:
            view.clear_search()
        if view is not None:
            view.terminal.grab_focus()

    def toggle_palette(self) -> None:
        if self.palette.get_visible():
            self.palette.hide()
            view = self.active_view
            if view is not None:
                view.terminal.grab_focus()
            return
        self.palette.set_entries(self._palette_entries())
        # GtkOverlay keeps a no-show-all overlay child at 1x1 until the next
        # layout pass, so opening is deferred to an idle callback.
        GLib.idle_add(self._open_palette)

    def _open_palette(self) -> bool:
        self.palette.open()
        return False

    def _palette_entries(self) -> List[Tuple[str, str, str, str]]:
        bindings = self.config.keybindings()

        def hint(action: str) -> str:
            return keymap.display(bindings.get(action, ""))

        entries: List[Tuple[str, str, str, str]] = [
            ("new_tab", "新建标签页", hint("new_tab"), "new tab 新标签"),
            ("close_tab", "关闭标签页", hint("close_tab"), "close tab 关闭"),
            ("reopen_tab", "重新打开已关闭的标签页", hint("reopen_tab"), "reopen 恢复"),
            ("next_tab", "下一个标签页", hint("next_tab"), "next tab 切换"),
            ("prev_tab", "上一个标签页", hint("prev_tab"), "previous tab 切换"),
            ("move_tab_left", "标签页左移", hint("move_tab_left"), "move tab 移动"),
            ("move_tab_right", "标签页右移", hint("move_tab_right"), "move tab 移动"),
            ("split_vertical", "垂直分屏（左右）", hint("split_vertical"), "split vertical 分屏"),
            ("split_horizontal", "水平分屏（上下）", hint("split_horizontal"), "split horizontal 分屏"),
            ("close_pane", "关闭当前分屏", hint("close_pane"), "close pane 分屏"),
            ("zoom_pane", "最大化当前分屏", hint("zoom_pane"), "zoom pane 放大"),
            ("focus_left", "聚焦左侧分屏", hint("focus_left"), "focus 分屏 焦点"),
            ("focus_right", "聚焦右侧分屏", hint("focus_right"), "focus 分屏 焦点"),
            ("focus_up", "聚焦上方分屏", hint("focus_up"), "focus 分屏 焦点"),
            ("focus_down", "聚焦下方分屏", hint("focus_down"), "focus 分屏 焦点"),
            ("find", "搜索终端内容", hint("find"), "search find 搜索 查找"),
            ("command_palette", "打开命令面板", hint("command_palette"), "palette command 命令"),
            ("copy", "复制选区", hint("copy"), "copy 复制"),
            ("paste", "粘贴", hint("paste"), "paste 粘贴"),
            ("paste_escaped", "粘贴为转义文本", hint("paste_escaped"), "escape paste 转义"),
            ("select_all", "全选", hint("select_all"), "select all 全选"),
            ("clear", "清屏", hint("clear"), "clear 清屏"),
            ("reset", "重置终端", hint("reset"), "reset 重置"),
            ("font_increase", "放大字号", hint("font_increase"), "font zoom 字号 放大"),
            ("font_decrease", "缩小字号", hint("font_decrease"), "font zoom 字号 缩小"),
            ("font_reset", "重置字号", hint("font_reset"), "font 字号 重置"),
            ("toggle_statusbar", "显示/隐藏状态栏", hint("toggle_statusbar"), "statusbar 状态栏"),
            ("toggle_sysinfo", "系统性能面板", hint("toggle_sysinfo"), "sysinfo cpu 性能 监控 资源"),
            ("toggle_filebrowser", "文件面板", hint("toggle_filebrowser"), "files 文件 浏览 目录 folder"),
            ("open_path", "打开路径 / 文件", hint("open_path"), "open file 打开 文件 路径"),
            ("open_selection", "打开选中的路径", hint("open_selection"), "open selection 打开 选区"),
            ("reveal_path", "在文件管理器中显示", hint("reveal_path"), "reveal 文件管理器 显示"),
            ("toggle_fullscreen", "切换全屏", hint("toggle_fullscreen"), "fullscreen 全屏"),
            ("open_preferences", "打开首选项", hint("open_preferences"), "preferences settings 设置"),
            ("new_window", "新建窗口", hint("new_window"), "new window 窗口"),
            ("about", "关于 Vela", "", "about 关于"),
            ("quit", "退出", hint("quit"), "quit exit 退出"),
        ]
        for name in theme_mod.all_names():
            entries.append(
                (
                    f"theme:{name}",
                    f"切换主题：{theme_mod.label(name)}",
                    "✓" if self.config.get("appearance.theme") == name else "",
                    f"theme 主题 {name}",
                )
            )
        return entries

    # ------------------------------------------------------------------
    # appearance
    # ------------------------------------------------------------------
    def set_theme(self, name: str, persist: bool = True) -> None:
        self.config.set("appearance.theme", name)
        self.theme = self.config.theme()
        self._refresh_css()
        for tab in self.tabs:
            tab.apply_theme(self.theme)
        if self.sysinfo_panel is not None:
            self.sysinfo_panel.apply_theme(self.theme)
        if self.filebrowser_panel is not None:
            self.filebrowser_panel.apply_theme(self.theme)
        for window in list(self._viewer_windows):
            window.apply_theme(self.theme)
        if persist:
            self._save_config()
        self.update_statusbar()
        self.show_status(f"主题：{theme_mod.label(name)}", 2)

    def change_font_size(self, delta: float) -> None:
        current = float(self.config.get("appearance.font_size"))
        target = max(5.0, min(72.0, current + delta))
        if abs(target - current) < 0.01:
            return
        self.config.set("appearance.font_size", target)
        self.config.set("appearance.use_system_font", False)
        self._apply_appearance()
        self._save_config()
        self.show_status(f"字号：{target:g}", 2)

    def reset_font_size(self) -> None:
        self.config.set("appearance.font_size", 12.0)
        self.config.set("appearance.use_system_font", False)
        self._apply_appearance()
        self._save_config()
        self.show_status("字号已重置为 12", 2)

    def _apply_appearance(self) -> None:
        for tab in self.tabs:
            tab.apply_appearance()
        self.update_statusbar()

    def apply_window_options(self) -> None:
        show_header = bool(self.config.get("window.show_headerbar"))
        self.headerbar.set_visible(show_header)
        self.statusbar.set_visible(bool(self.config.get("window.show_statusbar")))
        if self.sysinfo_panel is not None:
            self.sysinfo_panel.sampler.skip_loopback = bool(
                self.config.get("sysinfo.hide_loopback")
            )
            interval = max(500, int(float(self.config.get("sysinfo.interval")) * 1000))
            self.sysinfo_panel.stop()
            if self.sysinfo_panel.get_visible():
                self.sysinfo_panel._timer = GLib.timeout_add(
                    interval, self.sysinfo_panel._tick
                )
        if self.side_paned.get_visible():
            self.side_paned.set_size_request(
                int(self.config.get("window.sidebar_width")), -1
            )
        self.notebook.set_tab_pos(self._tab_position())
        self.update_tab_visibility()
        self._apply_appearance()

    def toggle_statusbar(self) -> None:
        visible = not self.statusbar.get_visible()
        self.statusbar.set_visible(visible)
        self.config.set("window.show_statusbar", visible)
        self._save_config()
        self.show_status("状态栏已" + ("显示" if visible else "隐藏"), 2)

    def toggle_fullscreen(self) -> None:
        if self._is_fullscreen:
            self.unfullscreen()
            self._is_fullscreen = False
        else:
            self.fullscreen()
            self._is_fullscreen = True

    def _tab_position(self) -> Gtk.PositionType:
        if self.config.get("window.tab_position") == "bottom":
            return Gtk.PositionType.BOTTOM
        return Gtk.PositionType.TOP

    def reload_config(self) -> None:
        self.config.reload()
        self.theme = self.config.theme()
        self._refresh_css()
        for tab in self.tabs:
            tab.apply_theme(self.theme)
        self.apply_window_options()
        self.show_status("配置已重新加载", 3)

    def _save_config(self) -> None:
        try:
            self.config.save()
        except OSError as error:
            self.show_status(f"无法保存配置：{error}", 6, "error")

    # ------------------------------------------------------------------
    # status bar
    # ------------------------------------------------------------------
    def show_status(
        self, message: str, seconds: float = 4.0, level: str = "info"
    ) -> None:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 4.0
        context = self.status_message.get_style_context()
        for name in ("vela-error", "vela-warning", "vela-status-accent"):
            context.remove_class(name)
        context.add_class(
            {"error": "vela-error", "warning": "vela-warning"}.get(
                level, "vela-status-accent"
            )
        )
        self.status_message.set_text(message)
        self.status_message.set_tooltip_text(message)
        if self._status_source:
            GLib.source_remove(self._status_source)
        self._status_source = GLib.timeout_add(
            max(500, int(seconds * 1000)), self._clear_status_message
        )

    def _clear_status_message(self) -> bool:
        self._status_source = 0
        self.status_message.set_text("")
        return False

    def update_statusbar(self) -> None:
        tab = self.active_tab
        view = tab.active_view if tab else None
        if view is None:
            self.status_dir.set_text("")
            self.status_panes.set_text("")
            self.status_theme.set_text("")
            self.status_cursor.set_text("")
            self.status_sysinfo.set_text("")
            return
        directory = view.directory or os.path.expanduser("~")
        self.status_dir.set_text(_shorten_path(directory))
        self.status_dir.set_tooltip_text(directory)
        parts = [view.shell_name]
        if tab.container.count() > 1:
            parts.append(f"{tab.container.count()} 分屏")
        self.status_panes.set_text(" · ".join(parts))
        self.status_theme.set_text(theme_mod.label(str(self.config.get("appearance.theme"))))
        self.status_cursor.set_text(self._cursor_text(view))

    def _cursor_text(self, view) -> str:
        try:
            column, row = view.terminal.get_cursor_position()
        except (TypeError, ValueError):
            return ""
        return f"行 {int(row) + 1} 列 {int(column) + 1}"

    # ------------------------------------------------------------------
    # dialogs
    # ------------------------------------------------------------------
    def _confirm_close_tab(self, tab: Tab) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="该标签页中仍有进程在运行",
        )
        dialog.format_secondary_text("关闭标签页会终止这些进程。")
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        close_button = dialog.add_button("关闭", Gtk.ResponseType.OK)
        close_button.get_style_context().add_class("destructive-action")
        remember = Gtk.CheckButton(label="以后不再询问")
        remember.show()
        dialog.get_content_area().add(remember)
        dialog.get_style_context().add_class("vela-dialog")
        response = dialog.run()
        remember_choice = remember.get_active()
        dialog.destroy()
        if remember_choice:
            self.config.set("behavior.confirm_close", False)
            self._save_config()
        return response == Gtk.ResponseType.OK

    def _on_delete_event(self, *_args) -> bool:
        if self._closing:
            return False
        busy = [
            tab for tab in self.tabs if self.config.get("behavior.confirm_close") and tab.any_busy()
        ]
        if busy:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text=f"仍有 {len(busy)} 个标签页在运行进程",
            )
            dialog.format_secondary_text("退出 Vela 会终止这些进程。")
            dialog.add_button("取消", Gtk.ResponseType.CANCEL)
            quit_button = dialog.add_button("退出", Gtk.ResponseType.OK)
            quit_button.get_style_context().add_class("destructive-action")
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return True
        self._closing = True
        self._save_window_geometry()
        for tab in list(self.tabs):
            tab.terminate_all()
        self.app.window_closed(self)
        return False

    def _save_window_geometry(self) -> None:
        # Gtk.Window has no is_fullscreen(); the state is tracked by our own
        # toggle so a fullscreen window does not overwrite the saved geometry.
        if self.is_maximized() or self._is_fullscreen:
            return
        width, height = self.get_size()
        if width > 200 and height > 200:
            self.config.set("window.width", width)
            self.config.set("window.height", height)
            self._save_config()

    def show_about(self) -> None:
        from . import APP_NAME, __version__

        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_program_name(APP_NAME)
        dialog.set_version(__version__)
        dialog.set_comments(
            "基于 GTK 3 + VTE 的现代终端，支持标签页、分屏、命令面板、主题与全文搜索。"
        )
        dialog.set_logo_icon_name("utilities-terminal")
        dialog.set_license_type(Gtk.License.MIT_X11)
        dialog.get_style_context().add_class("vela-dialog")
        dialog.run()
        dialog.destroy()

    def open_preferences(self) -> None:
        from .prefs import PreferencesDialog

        if self._prefs_dialog is not None:
            self._prefs_dialog.present()
            return
        self._prefs_dialog = PreferencesDialog(self)
        self._prefs_dialog.connect("destroy", self._on_prefs_destroyed)
        self._prefs_dialog.show_all()

    def _on_prefs_destroyed(self, *_args) -> None:
        self._prefs_dialog = None

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------
    def pane_exited(self, tab: Tab, view) -> None:
        """A shell in ``tab`` exited (typically because the user typed ``exit``).

        Standard terminal behaviour, in order:

        * close the pane the shell was in;
        * if that was the tab's last pane, close the tab;
        * if that was the last tab, close the window.

        An abnormal exit keeps the pane so the error output stays readable,
        unless ``behavior.close_on_abnormal_exit`` is set.
        """
        if not self.config.get("behavior.close_pane_on_exit"):
            self.show_status(f"进程已退出（{_exit_label(view.exit_status)}）", 4)
            return
        if not _clean_exit(view.exit_status) and not self.config.get(
            "behavior.close_on_abnormal_exit"
        ):
            self.show_status(
                f"进程异常退出（{_exit_label(view.exit_status)}），已保留分屏", 8, "warning"
            )
            return
        # Deferred: this is called from the terminal's child-exited signal.
        GLib.idle_add(self._apply_pane_exit, tab, view)

    def _apply_pane_exit(self, tab: Tab, view) -> bool:
        if tab not in self.tabs:
            return False
        leaf = tab._leaf_for(view)
        if leaf is None:
            return False
        if tab.container.count() <= 1:
            self.show_status(f"标签页已关闭（{_exit_label(view.exit_status)}）", 3)
            self.close_tab(tab, force=True, from_shell_exit=True)
            return False
        was_active = tab.container.active is leaf
        tab.container.close(leaf)
        if was_active:
            tab.container.set_active(tab.container.active)
        tab.refresh_title()
        self.update_statusbar()
        self.refresh_window_title()
        self.show_status(f"分屏已关闭（{_exit_label(view.exit_status)}）", 3)
        return False

    def window_exited(self) -> None:
        """Close this window after its last shell exited."""
        if self._closing:
            return
        # Route through the normal close path so the window geometry is saved and
        # any still-running shells in other tabs get the usual confirmation.
        self.emit("delete-event", Gdk.Event.new(Gdk.EventType.DELETE))

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == 65307:  # Escape
            closed = False
            if self.palette.get_visible():
                self.palette.hide()
                closed = True
            if self.search_revealer.get_reveal_child():
                self.search_bar.close()
                closed = True
            if closed:
                return True
        return False


def _shorten_path(path: str, limit: int = 52) -> str:
    home = os.path.expanduser("~")
    if path.startswith(home):
        path = "~" + path[len(home) :]
    if len(path) <= limit:
        return path
    parts = path.split(os.sep)
    if len(parts) <= 2:
        return path
    return os.sep.join([parts[0], "…", *parts[-2:]])


def _exit_code(status: Optional[int]) -> Optional[int]:
    """Shell exit code from a wait status, or ``None`` when not a clean exit."""
    if status is None:
        return None
    try:
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    return None


def _clean_exit(status: Optional[int]) -> bool:
    """Whether the shell exited normally with code 0."""
    return _exit_code(status) == 0


def _exit_label(status: Optional[int]) -> str:
    """Human-readable exit reason for the status bar."""
    code = _exit_code(status)
    if code is None:
        return "退出状态未知"
    if code >= 128:
        return f"被信号 {code - 128} 终止"
    return f"退出码 {code}"
