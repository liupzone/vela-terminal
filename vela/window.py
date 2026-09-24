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
from . import layout as layout_mod  # noqa: E402
from . import panels as panels_mod  # noqa: E402
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
        # Panels are created per pane, so there is no single shared instance.
        self.sidebar_visible = False
        self._action_callbacks: Dict[str, Callable] = {}
        self._directory_poll = 0

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
    # side panels (as split panes)
    # ------------------------------------------------------------------
    def create_sysinfo_panel(self) -> sysinfo_mod.SysinfoPanel:
        """Build a system-monitor panel for a pane.

        Panels are created per pane rather than shared, so the same panel can
        appear in several splits at once.  Each one starts sampling when its pane
        is created and stops when the pane is closed.
        """
        panel = sysinfo_mod.SysinfoPanel(
            self.config,
            self.theme,
            pane_provider=self._pane_process_list,
            notify=self.show_status,
        )
        panel.on_snapshot = self._on_sysinfo_snapshot
        panel.set_size_request(200, -1)
        return panel

    def start_sysinfo_pane(self) -> None:
        """Sampling hook used by PanelView for a system-monitor pane."""
        tab = self.active_tab
        if tab is None:
            return
        view = tab.active_view
        panel = getattr(view, "panel", None)
        if isinstance(panel, sysinfo_mod.SysinfoPanel):
            panel.start()

    def stop_sysinfo_pane(self) -> None:
        tab = self.active_tab
        if tab is None:
            return
        view = tab.active_view
        panel = getattr(view, "panel", None)
        if isinstance(panel, sysinfo_mod.SysinfoPanel):
            panel.stop()

    def _on_sysinfo_snapshot(self, snapshot) -> None:
        """Mirror a panel's reading into the status bar."""
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
        for view in tab.terminals():
            if view.child_pid and not view.exited:
                rows.append((view.title, view.child_pid))
        return rows

    def create_filebrowser_panel(self) -> "filebrowser_mod.FileBrowserPanel":
        panel = filebrowser_mod.FileBrowserPanel(
            self.config,
            self.theme,
            on_open=self.open_path,
            notify=self.show_status,
        )
        # The panel asks for the terminal's directory whenever it needs it, so
        # following stays correct even after switching tabs.
        panel.terminal_directory = self._terminal_directory
        panel.on_follow_changed = self._sync_follow_poll
        panel.following = bool(self.config.get("filebrowser.follow_terminal"))
        panel.set_size_request(200, -1)
        directory = self._terminal_directory()
        if directory:
            panel.set_directory(directory, force=not panel.following)
        if not panel.directory:
            panel.navigate(os.path.expanduser("~"))
        self._sync_follow_poll()
        return panel

    def _terminal_directory(self) -> str:
        tab = self.active_tab
        view = tab.active_terminal() if tab is not None else None
        if view is None:
            return ""
        # Sampling /proc keeps this correct even when the shell never reports a
        # directory to VTE (see TerminalView.refresh_directory).
        refresher = getattr(view, "refresh_directory", None)
        directory = refresher() if refresher is not None else view.directory
        return directory or os.path.expanduser("~")

    def current_directory(self) -> Optional[str]:
        """Directory a new terminal pane should start in."""
        directory = self._terminal_directory()
        if directory and os.path.isdir(directory):
            return directory
        return None

    def _panes_of_kind(self, kind: str) -> List:
        tab = self.active_tab
        if tab is None:
            return []
        return [
            leaf
            for leaf in tab.container.leaves()
            if getattr(leaf, "kind", panels_mod.KIND_TERMINAL) == kind
        ]

    def split_pane_as(self, kind: str, orientation: Optional[Gtk.Orientation] = None) -> None:
        """Split the active pane, putting ``kind`` in the new half."""
        tab = self.active_tab
        if tab is None:
            return
        if orientation is None:
            orientation = Gtk.Orientation.HORIZONTAL
        leaf = tab.split(orientation, cwd=self.current_directory(), kind=kind)
        if leaf is None:
            return
        self.update_statusbar()
        self.refresh_window_title()
        # A new file panel is only in the tree now, so the follow poll has to be
        # reconsidered after the split rather than while the panel is built.
        self._sync_follow_poll()
        self.show_status(f"已新建{panels_mod.kind_label(kind)}分屏", 2)

    def toggle_sysinfo(self) -> None:
        """Add or remove a system-monitor pane in the current tab."""
        tab = self.active_tab
        if tab is None:
            return
        existing = self._panes_of_kind(panels_mod.KIND_SYSINFO)
        if existing:
            for leaf in existing:
                self._close_pane_leaf(leaf)
            self.show_status("系统性能分屏已关闭", 2)
            return
        self.split_pane_as(panels_mod.KIND_SYSINFO)

    def toggle_filebrowser(self) -> None:
        tab = self.active_tab
        if tab is None:
            return
        existing = self._panes_of_kind(panels_mod.KIND_FILEBROWSER)
        if existing:
            for leaf in existing:
                self._close_pane_leaf(leaf)
            self.show_status("文件分屏已关闭", 2)
            return
        self.split_pane_as(panels_mod.KIND_FILEBROWSER)

    def _close_pane_leaf(self, leaf) -> None:
        tab = self.active_tab
        if tab is None:
            return
        view = leaf.view
        terminator = getattr(view, "terminate", None)
        if terminator is not None:
            terminator()
        if tab.container.count() <= 1:
            return
        tab.container.close(leaf)
        tab.refresh_title()
        self.update_statusbar()
        self.refresh_window_title()
        self._sync_follow_poll()

    def _sysinfo_visible(self) -> bool:
        return bool(self._panes_of_kind(panels_mod.KIND_SYSINFO))

    def _filebrowser_visible(self) -> bool:
        return bool(self._panes_of_kind(panels_mod.KIND_FILEBROWSER))

    def switch_active_pane_kind(self, kind: str) -> None:
        """Change what the active split shows, keeping its position.

        Switching the last terminal away would leave a tab with no way to type,
        so a terminal pane is opened alongside it first.
        """
        tab = self.active_tab
        if tab is None:
            return
        was_last_terminal = (
            not panels_mod.is_terminal(kind)
            and len(tab.terminals()) <= 1
            and panels_mod.is_terminal(
                getattr(tab.container.active, "kind", panels_mod.KIND_TERMINAL)
            )
        )
        original = tab.container.active
        if was_last_terminal:
            # Open the replacement terminal in the *new* half, so the pane the
            # user is looking at keeps its position and the terminal appears
            # beside it ("panel | terminal" when the panel was on the left).
            tab.split(
                Gtk.Orientation.HORIZONTAL,
                cwd=self.current_directory(),
                kind=panels_mod.KIND_TERMINAL,
            )
            # Switch the pane the user started from, not the new terminal.
            tab.container.set_active(original)
        leaf = tab.replace_active_kind(kind)
        if leaf is None:
            self.show_status("没有可切换的分屏", 4, "warning")
            return
        self.update_statusbar()
        self.refresh_window_title()
        message = f"当前分屏已切换为{panels_mod.kind_label(kind)}"
        if was_last_terminal:
            message += "（已保留一个终端分屏）"
        self.show_status(message, 3)

    def _sync_sidebar(self) -> None:
        """Kept for compatibility; panels are panes now, not a sidebar."""
        self.sidebar_visible = self._sysinfo_visible() or self._filebrowser_visible()
        self.update_statusbar()

    def set_sysinfo_visible(self, visible: bool, persist: bool = True) -> None:
        """Compatibility wrapper: add or remove a monitor pane."""
        tab = self.active_tab
        if tab is None:
            return
        existing = self._panes_of_kind(panels_mod.KIND_SYSINFO)
        if visible and not existing:
            self.split_pane_as(panels_mod.KIND_SYSINFO)
        elif not visible and existing:
            for leaf in existing:
                self._close_pane_leaf(leaf)
        if persist:
            self.config.set("sysinfo.enabled", visible)
            self._save_config()
        self.update_statusbar()

    def set_filebrowser_visible(self, visible: bool, persist: bool = True) -> None:
        """Compatibility wrapper: add or remove a file-browser pane."""
        tab = self.active_tab
        if tab is None:
            return
        existing = self._panes_of_kind(panels_mod.KIND_FILEBROWSER)
        if visible and not existing:
            self.split_pane_as(panels_mod.KIND_FILEBROWSER)
        elif not visible and existing:
            for leaf in existing:
                self._close_pane_leaf(leaf)
        if persist:
            self.config.set("filebrowser.enabled", visible)
            self._save_config()
        self.update_statusbar()

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
        view = self.active_terminal
        if view is None:
            return ""
        try:
            return view.path_under_cursor()
        except Exception as error:  # pragma: no cover - defensive
            self.show_status(f"无法识别路径：{error}", 6, "error")
            return ""

    def open_selection(self) -> bool:
        view = self.active_terminal
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
        file_section.append("重命名标签页…", "win.rename-tab")
        menu.append_section(None, file_section)

        layout_section = Gio.Menu()
        layout_section.append("保存当前布局…", "win.save-layout")
        layout_section.append("恢复布局…", "win.restore-layout")
        saved = layout_mod.named_layouts(self.config)
        if saved:
            restore_menu = Gio.Menu()
            for name in sorted(saved):
                item = Gio.MenuItem.new(name, None)
                item.set_action_and_target_value(
                    "win.restore-layout-named", GLib.Variant.new_string(name)
                )
                restore_menu.append_item(item)
            layout_section.append_submenu("恢复已保存的布局", restore_menu)
        menu.append_section(None, layout_section)

        split_section = Gio.Menu()
        split_section.append("垂直分屏", "win.split-vertical")
        split_section.append("水平分屏", "win.split-horizontal")
        split_section.append("关闭当前分屏", "win.close-pane")
        split_section.append("最大化当前分屏", "win.zoom-pane")
        panel_section = Gio.Menu()
        panel_section.append("新建系统性能分屏", "win.split-sysinfo")
        panel_section.append("新建文件面板分屏", "win.split-filebrowser")
        panel_section.append("当前分屏切换为终端", "win.pane-to-terminal")
        panel_section.append("当前分屏切换为系统性能", "win.pane-to-sysinfo")
        panel_section.append("当前分屏切换为文件面板", "win.pane-to-filebrowser")
        menu.append_section(None, panel_section)
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
        # Tab naming and the tab menu hang off the notebook rather than off the
        # individual tab labels: a label is a Gtk.Box with no window of its own,
        # so GTK never dispatches a press to it.  The notebook does get the
        # press, with coordinates relative to itself (see _tab_at).
        self.notebook.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.notebook.connect("button-press-event", self._on_notebook_button)

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

        # Side panels are ordinary split panes now, so the terminal area is the
        # whole window body and the panels live inside the notebook's pane trees.
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.body.pack_start(self.overlay, True, True, 0)
        self.add(self.body)

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
            ("split-sysinfo", lambda *_: self.split_pane_as(
                panels_mod.KIND_SYSINFO, Gtk.Orientation.HORIZONTAL)),
            ("split-filebrowser", lambda *_: self.split_pane_as(
                panels_mod.KIND_FILEBROWSER, Gtk.Orientation.HORIZONTAL)),
            ("pane-to-terminal", lambda *_: self.switch_active_pane_kind(
                panels_mod.KIND_TERMINAL)),
            ("pane-to-sysinfo", lambda *_: self.switch_active_pane_kind(
                panels_mod.KIND_SYSINFO)),
            ("pane-to-filebrowser", lambda *_: self.switch_active_pane_kind(
                panels_mod.KIND_FILEBROWSER)),
            ("rename-tab", lambda *_: self.rename_tab()),
            ("save-layout", lambda *_: self.save_layout_dialog()),
            ("restore-layout", lambda *_: self.restore_layout_dialog()),
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

        named = Gio.SimpleAction.new("restore-layout-named", GLib.VariantType.new("s"))
        named.connect(
            "activate",
            lambda _a, param: self.restore_layout_by_name(param.get_string()),
        )
        self.add_action(named)

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
        """The active pane's view: a terminal or a side panel."""
        tab = self.active_tab
        return tab.active_view if tab else None

    @property
    def active_terminal(self):
        """The terminal a terminal-only action should act on.

        When a panel pane is active, terminal actions fall back to the nearest
        terminal in the same tab instead of silently doing nothing.
        """
        tab = self.active_tab
        return tab.active_terminal() if tab else None

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
        self._sync_follow_poll()

    def follow_terminal_directory(self) -> None:
        """Point every following file panel at the active terminal's directory."""
        directory = self._terminal_directory()
        if not directory:
            return
        for leaf in self._panes_of_kind(panels_mod.KIND_FILEBROWSER):
            panel = getattr(leaf.view, "panel", None)
            if panel is None or not getattr(panel, "following", False):
                continue
            if os.path.abspath(directory) != os.path.abspath(panel.directory or ""):
                panel.set_directory(directory)

    def _sync_follow_poll(self) -> None:
        """Poll the shell's directory only while a file panel is following it.

        The directory change has to be noticed without a signal: VTE emits one
        only when the shell runs its integration hook, and the hook is installed
        by ``PROMPT_COMMAND``, which a non-login shell never runs (see
        ``TerminalView.refresh_directory``).  A ``cd`` typed straight into the
        terminal would otherwise never move the panel.

        The timer exists only while a following panel is on screen, so a session
        with no file panel pays nothing.
        """
        wanted = any(
            getattr(getattr(leaf.view, "panel", None), "following", False)
            for leaf in self._panes_of_kind(panels_mod.KIND_FILEBROWSER)
        )
        if wanted and not self._directory_poll:
            self._directory_poll = GLib.timeout_add(500, self._poll_directory)
        elif not wanted and self._directory_poll:
            GLib.source_remove(self._directory_poll)
            self._directory_poll = 0

    def _poll_directory(self) -> bool:
        if not self._panes_of_kind(panels_mod.KIND_FILEBROWSER):
            self._directory_poll = 0
            return False
        self.follow_terminal_directory()
        return True

    def update_tab_visibility(self) -> None:
        mode = self.config.get("window.show_tabbar")
        count = self.notebook.get_n_pages()
        show = mode == "always" or (mode == "multiple" and count > 1)
        self.notebook.set_show_tabs(show)

    # ------------------------------------------------------------------
    # layouts
    # ------------------------------------------------------------------
    def capture_layout(self, name: str = "") -> layout_mod.LayoutSpec:
        """Snapshot the current tabs and pane trees."""
        return layout_mod.LayoutSpec(
            name=name, tabs=[tab.snapshot() for tab in self.tabs]
        )

    def apply_layout(self, spec: layout_mod.LayoutSpec) -> None:
        """Replace the current tabs with the ones described by ``spec``."""
        spec = layout_mod.normalize(spec)
        moved = layout_mod.prune_missing_directories(spec)
        for tab in list(self.tabs):
            self._closing_ok = True
            tab.terminate_all()
            page = self.notebook.page_num(tab.container)
            if page >= 0:
                self.notebook.remove_page(page)
            if tab in self.tabs:
                self.tabs.remove(tab)
        for tab_spec in spec.tabs:
            tab = Tab(self, self.config, self.theme, notify=self.show_status)
            page = self.notebook.append_page(tab.container, tab.label)
            self.notebook.set_tab_reorderable(tab.container, True)
            self.notebook.set_tab_detachable(tab.container, True)
            self.tabs.append(tab)
            tab.container.show_all()
            tab.label.show_all()
            tab.restore(tab_spec)
            tab.refresh_title()
            self.notebook.set_current_page(page)
        self.notebook.show_all()
        if not self.tabs:
            self.new_tab()
        self.update_tab_visibility()
        self.update_statusbar()
        self.refresh_window_title()
        if moved:
            self.show_status(
                f"布局已恢复；{len(moved)} 个目录已不存在，改用主目录", 6, "warning"
            )
        else:
            self.show_status("布局已恢复", 3)

    def save_layout_dialog(self, name: str = "") -> None:
        """Ask for a name and store the current layout under it."""
        dialog = Gtk.Dialog(title="保存布局", transient_for=self, modal=True)
        dialog.get_style_context().add_class("vela-dialog")
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        ok_button = dialog.add_button("保存", Gtk.ResponseType.OK)
        ok_button.get_style_context().add_class("suggested-action")
        entry = Gtk.Entry()
        entry.set_text(name)
        entry.set_placeholder_text("例如：开发、部署、日志")
        entry.set_width_chars(24)
        entry.connect("activate", lambda *_: dialog.response(Gtk.ResponseType.OK))
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.add(entry)
        hint = Gtk.Label(
            label=(
                f"将保存 {len(self.tabs)} 个标签页、"
                f"{self.capture_layout().pane_count} 个分屏的结构与目录。"
                "终端的历史输出不会被保存。"
            )
        )
        hint.set_xalign(0.0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("vela-dim")
        box.add(hint)
        existing = sorted(layout_mod.named_layouts(self.config))
        if existing:
            list_hint = Gtk.Label(label="已保存：" + "、".join(existing))
            list_hint.set_xalign(0.0)
            list_hint.set_line_wrap(True)
            list_hint.get_style_context().add_class("vela-dim")
            box.add(list_hint)
        dialog.show_all()
        entry.grab_focus()
        entry.select_region(0, -1)
        response = dialog.run()
        chosen = entry.get_text()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self.save_layout(chosen)

    def save_layout(self, name: str) -> bool:
        spec = self.capture_layout(name)
        try:
            key = layout_mod.store_named_layout(self.config, spec, name)
        except layout_mod.LayoutError as error:
            self.show_status(str(error), 6, "error")
            return False
        self._save_config()
        self.show_status(f"布局已保存为「{key}」", 4)
        return True

    def restore_layout_dialog(self) -> None:
        """Pick a saved layout and apply it."""
        saved = layout_mod.named_layouts(self.config)
        if not saved:
            self.show_status("还没有保存过布局；先用「保存当前布局」", 5, "warning")
            return
        dialog = Gtk.Dialog(title="恢复布局", transient_for=self, modal=True)
        dialog.get_style_context().add_class("vela-dialog")
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        restore = dialog.add_button("恢复", Gtk.ResponseType.OK)
        restore.get_style_context().add_class("suggested-action")
        combo = Gtk.ComboBoxText()
        for name in sorted(saved):
            spec = saved[name]
            combo.append_text(f"{name}（{len(spec.tabs)} 标签 / {spec.pane_count} 分屏）")
        combo.set_active(0)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.add(combo)
        hint = Gtk.Label(label="恢复会替换当前所有标签页。")
        hint.set_xalign(0.0)
        hint.get_style_context().add_class("vela-dim")
        box.add(hint)
        dialog.show_all()
        response = dialog.run()
        index = combo.get_active()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or index < 0:
            return
        name = sorted(saved)[index]
        self.apply_layout(saved[name])

    def restore_layout_by_name(self, name: str) -> bool:
        """Apply a named layout; used by the command palette and the menu."""
        saved = layout_mod.named_layouts(self.config)
        spec = saved.get(name)
        if spec is None:
            self.show_status(f"找不到布局「{name}」", 5, "error")
            return False
        self.apply_layout(spec)
        return True

    def delete_layout(self, name: str) -> bool:
        if layout_mod.remove_named_layout(self.config, name):
            self._save_config()
            self.show_status(f"布局「{name}」已删除", 3)
            return True
        return False

    # -- session ---------------------------------------------------------
    def save_session(self) -> bool:
        """Persist the current layout so the next launch can restore it."""
        if not self.config.get("behavior.restore_session"):
            return False
        try:
            layout_mod.save_session(self.capture_layout("session"))
            return True
        except OSError as error:
            self.show_status(f"无法保存会话：{error}", 6, "error")
            return False

    def restore_session_if_enabled(self) -> bool:
        """Restore the previous session when the setting allows it."""
        if not self.config.get("behavior.restore_session"):
            return False
        spec = layout_mod.load_session()
        if spec is None:
            return False
        self.apply_layout(spec)
        self.show_status("已恢复上次的会话", 3)
        return True

    # ------------------------------------------------------------------
    # tab naming
    # ------------------------------------------------------------------
    def _tab_at(self, x: int, y: int) -> Optional[Tab]:
        """The tab whose label is under the given notebook coordinates.

        ``x``/``y`` arrive relative to the notebook, but a tab label's
        allocation is not: GtkNotebook stores it in the coordinate space of the
        notebook's own window, which is offset by the notebook's position.  The
        point is therefore translated into each label's space instead of being
        compared against ``get_allocation()`` directly.
        """
        if not self.notebook.get_show_tabs():
            return None
        for index in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(index)
            label = self.notebook.get_tab_label(page)
            if label is None:
                continue
            try:
                point = label.translate_coordinates(self.notebook, 0, 0)
            except (TypeError, ValueError):
                continue
            if point is None:
                continue
            left, top = point[0], point[1]
            allocation = label.get_allocation()
            if (
                left <= x <= left + allocation.width
                and top <= y <= top + allocation.height
            ):
                return self.tab_for_widget(page)
        return None

    def _on_notebook_button(self, _widget, event) -> bool:
        """Double-click renames a tab; right-click opens the tab menu."""
        tab = self._tab_at(int(event.x), int(event.y))
        if tab is None:
            return False
        if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:
            self.rename_tab(tab)
            return True
        if event.button == 3:
            self.notebook.set_current_page(self.notebook.page_num(tab.container))
            self._show_tab_menu(tab, event)
            return True
        return False

    def _show_tab_menu(self, tab: Tab, event) -> None:
        menu = Gtk.Menu()

        def add(label: str, callback: Callable, sensitive: bool = True) -> None:
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(sensitive)
            item.connect("activate", lambda *_: callback())
            item.show()
            menu.append(item)

        add("重命名标签页…", lambda: self.rename_tab(tab))
        add("恢复跟随 shell 标题", lambda: self.rename_tab(tab, clear=True),
            sensitive=tab.is_named())
        menu.append(Gtk.SeparatorMenuItem())
        add("新建标签页", lambda: self.new_tab())
        add("关闭标签页", lambda: self.close_tab(tab))
        add("重新打开已关闭的标签页", lambda: self.reopen_tab())
        menu.append(Gtk.SeparatorMenuItem())
        add("保存当前布局…", lambda: self.save_layout_dialog())
        add("恢复布局…", lambda: self.restore_layout_dialog())
        menu.show_all()
        menu.popup_at_pointer(event)

    def rename_tab(self, tab: Optional[Tab] = None, clear: bool = False) -> None:
        """Ask for a tab name; an empty answer restores the shell title."""
        tab = tab or self.active_tab
        if tab is None:
            return
        if clear:
            tab.set_name("")
            self.show_status("标签页已恢复跟随 shell 标题", 3)
            return
        dialog = Gtk.Dialog(
            title="重命名标签页", transient_for=self, modal=True
        )
        dialog.get_style_context().add_class("vela-dialog")
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        ok_button = dialog.add_button("确定", Gtk.ResponseType.OK)
        ok_button.get_style_context().add_class("suggested-action")
        entry = Gtk.Entry()
        entry.set_text(tab.title_override)
        entry.set_placeholder_text("留空则跟随 shell 标题")
        entry.set_width_chars(28)
        entry.connect("activate", lambda *_: dialog.response(Gtk.ResponseType.OK))
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.add(entry)
        hint = Gtk.Label(label="命名后标签页不再随 shell 标题变化。")
        hint.set_xalign(0.0)
        hint.get_style_context().add_class("vela-dim")
        box.add(hint)
        dialog.show_all()
        entry.grab_focus()
        entry.select_region(0, -1)
        response = dialog.run()
        name = entry.get_text()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        tab.set_name(name)
        if name.strip():
            self.show_status(f"标签页已重命名为「{name.strip()}」", 3)
        else:
            self.show_status("标签页已恢复跟随 shell 标题", 3)

    def refresh_tab_titles(self) -> None:
        for tab in self.tabs:
            # A tab is named after its terminal, never after a panel pane: making
            # the file browser the active split must not rename the tab.
            view = tab.active_terminal()
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
        # The title follows the active pane, but the directory and pane count
        # come from the terminal side so a panel pane still shows where it is.
        terminal = tab.active_terminal()
        directory = getattr(terminal, "directory", "") if terminal else ""
        subtitle = _shorten_path(directory) if directory else ""
        if view is not None and not panels_mod.is_terminal(
            getattr(view, "kind", panels_mod.KIND_TERMINAL)
        ):
            label = panels_mod.kind_label(getattr(view, "kind", ""))
            subtitle = f"{subtitle} · {label}" if subtitle else label
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
    def _with_view(self, callback: Callable, terminal_only: bool = True) -> bool:
        """Run ``callback`` on the pane a terminal action should target."""
        view = self.active_terminal if terminal_only else self.active_view
        if view is None:
            if terminal_only:
                self.show_status("当前标签页没有终端分屏", 4, "warning")
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
            view = self.active_terminal
            if view is not None and view.terminal.get_has_selection():
                selected = view.get_selected_text().strip()
            self.search_bar.open(selected if len(selected) < 120 else "")

    def _on_search(self, pattern: str, regex: bool, forward: bool) -> bool:
        view = self.active_terminal
        if view is None:
            return False
        return view.search(pattern, regex=regex, forward=forward)

    def _on_search_closed(self) -> None:
        self.search_revealer.set_reveal_child(False)
        view = self.active_terminal
        if view is not None:
            view.clear_search()
        if view is not None:
            view.terminal.grab_focus()

    def toggle_palette(self) -> None:
        if self.palette.get_visible():
            self.palette.hide()
            view = self.active_terminal
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
            ("split_sysinfo", "新建系统性能分屏", "", "split sysinfo 性能 分屏"),
            ("split_filebrowser", "新建文件面板分屏", "", "split files 文件 分屏"),
            ("pane_to_terminal", "当前分屏切换为终端", "", "pane terminal 切换 终端"),
            ("pane_to_sysinfo", "当前分屏切换为系统性能", "", "pane sysinfo 切换 性能"),
            ("pane_to_filebrowser", "当前分屏切换为文件面板", "", "pane files 切换 文件"),
            ("rename_tab", "重命名标签页", hint("rename_tab"), "rename tab 重命名 标签"),
            ("save_layout", "保存当前布局", hint("save_layout"), "layout save 保存 布局"),
            ("restore_layout", "恢复布局", hint("restore_layout"), "layout restore 恢复 布局"),
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
        if tab is None:
            self.status_dir.set_text("")
            self.status_panes.set_text("")
            self.status_theme.set_text("")
            self.status_cursor.set_text("")
            self.status_sysinfo.set_text("")
            return
        # The status bar describes the terminal being worked in; when a panel
        # pane is active, the nearest terminal supplies directory and cursor.
        view = tab.active_terminal()
        active = tab.active_view
        if view is None:
            self.status_dir.set_text("")
            self.status_cursor.set_text("")
        else:
            directory = view.directory or os.path.expanduser("~")
            self.status_dir.set_text(_shorten_path(directory))
            self.status_dir.set_tooltip_text(directory)
            self.status_cursor.set_text(self._cursor_text(view))
        parts: List[str] = []
        if view is not None:
            parts.append(view.shell_name)
        if active is not None and not panels_mod.is_terminal(
            getattr(active, "kind", panels_mod.KIND_TERMINAL)
        ):
            parts.append(panels_mod.kind_label(getattr(active, "kind", "")))
        if tab.container.count() > 1:
            parts.append(f"{tab.container.count()} 分屏")
        self.status_panes.set_text(" · ".join(parts))
        self.status_theme.set_text(theme_mod.label(str(self.config.get("appearance.theme"))))
        self.status_sysinfo.set_text(self._sysinfo_status_text())

    def _sysinfo_status_text(self) -> str:
        """CPU/memory summary from any live monitor pane."""
        for leaf in self._panes_of_kind(panels_mod.KIND_SYSINFO):
            panel = getattr(leaf.view, "panel", None)
            snapshot = getattr(panel, "last_snapshot", None)
            if snapshot is not None:
                return (
                    f"CPU {snapshot.cpu_percent:.0f}% · "
                    f"内存 {snapshot.memory.percent:.0f}%"
                )
        return ""

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
        if self._directory_poll:
            GLib.source_remove(self._directory_poll)
            self._directory_poll = 0
        self.save_session()
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
