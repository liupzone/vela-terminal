"""Preferences dialog: theme, font, cursor, scrollback and behaviour."""

from __future__ import annotations

import threading
from typing import Dict

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import fonts as fonts_mod  # noqa: E402
from . import keymap, theme as theme_mod  # noqa: E402


def _css_family(value: str) -> str:
    """Turn a font fallback list into a CSS ``font-family`` value.

    Names are quoted because family names contain spaces and commas separate
    the fallbacks.
    """
    parts = [part.strip() for part in (value or "").split(",") if part.strip()]
    if not parts:
        return "monospace"
    return ", ".join('"' + part.replace('"', "") + '"' for part in parts)


class PreferencesDialog(Gtk.Dialog):
    """Edits the in-memory config and applies changes live."""

    def __init__(self, window) -> None:
        super().__init__(title="Vela 首选项", transient_for=window, modal=False)
        # Gtk.Widget already defines a read-only "window" property, so the parent
        # window is kept under a name that does not clash with it.
        self.parent_window = window
        self.config = window.config
        self.get_style_context().add_class("vela-dialog")
        self.set_default_size(560, 620)
        self.set_border_width(0)
        self.add_button("关闭", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())

        self._dirty = False
        self._suspend = False
        self._bindings: Dict[str, Gtk.Widget] = {}

        notebook = Gtk.Notebook()
        notebook.set_show_border(False)
        notebook.get_style_context().add_class("vela-notebook")
        notebook.append_page(self._build_appearance(), Gtk.Label(label="外观"))
        notebook.append_page(self._build_fonts_page(), Gtk.Label(label="字体"))
        notebook.append_page(self._build_behavior(), Gtk.Label(label="行为"))
        notebook.append_page(self._build_window_page(), Gtk.Label(label="窗口"))
        notebook.append_page(self._build_panels_page(), Gtk.Label(label="面板与文件"))
        notebook.append_page(self._build_shortcuts(), Gtk.Label(label="快捷键"))
        # The notebook must take the full height of the dialog; without this it
        # keeps its natural height and the page content is squeezed into a strip
        # at the top with empty space below.
        notebook.set_vexpand(True)
        notebook.set_hexpand(True)
        content = self.get_content_area()
        content.set_vexpand(True)
        content.add(notebook)

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------
    def _build_appearance(self) -> Gtk.Widget:
        grid = self._grid()
        row = 0

        self._add_section(grid, row, "配色")
        row += 1
        theme_combo = Gtk.ComboBoxText()
        for name in theme_mod.all_names():
            theme_combo.append(name, theme_mod.label(name))
        theme_combo.set_active_id(str(self.config.get("appearance.theme")))
        theme_combo.connect("changed", self._on_theme_changed)
        row = self._add_row(grid, row, "主题", theme_combo)

        self._add_section(grid, row, "字体")
        row += 1
        font_summary = Gtk.Label()
        font_summary.set_xalign(0.0)
        font_summary.set_ellipsize(3)
        font_summary.get_style_context().add_class("vela-dim")
        self._font_summary = font_summary
        row = self._add_row(grid, row, "当前字体", font_summary)
        self._refresh_font_summary()

        fonts_button = Gtk.Button(label="在「字体」页修改")
        fonts_button.connect("clicked", lambda *_: self._goto_fonts_page())
        row = self._add_row(grid, row, "", fonts_button)

        system_font = Gtk.Switch()
        system_font.set_active(bool(self.config.get("appearance.use_system_font")))
        system_font.connect("notify::active", self._on_system_font)
        row = self._add_row(grid, row, "跟随系统字体", system_font)

        size = Gtk.SpinButton.new_with_range(5, 72, 1)
        size.set_value(float(self.config.get("appearance.font_size")))
        size.connect("value-changed", self._on_font_size)
        row = self._add_row(grid, row, "字号", size)

        self._add_section(grid, row, "光标与滚动")
        row += 1
        shape = Gtk.ComboBoxText()
        for value, label in (("block", "方块"), ("ibeam", "竖线"), ("underline", "下划线")):
            shape.append(value, label)
        shape.set_active_id(str(self.config.get("appearance.cursor_shape")))
        shape.connect("changed", self._on_cursor_shape)
        row = self._add_row(grid, row, "光标形状", shape)

        blink = Gtk.ComboBoxText()
        for value, label in (("system", "跟随系统"), ("on", "闪烁"), ("off", "常亮")):
            blink.append(value, label)
        blink.set_active_id(str(self.config.get("appearance.cursor_blink")))
        blink.connect("changed", self._on_cursor_blink)
        row = self._add_row(grid, row, "光标闪烁", blink)

        scrollback = Gtk.SpinButton.new_with_range(0, 1000000, 1000)
        scrollback.set_value(float(self.config.get("appearance.scrollback_lines")))
        scrollback.connect("value-changed", self._on_scrollback)
        row = self._add_row(grid, row, "回滚行数", scrollback)

        padding = Gtk.SpinButton.new_with_range(0, 40, 1)
        padding.set_value(float(self.config.get("appearance.padding")))
        padding.connect("value-changed", self._on_padding)
        row = self._add_row(grid, row, "内边距", padding)

        self._add_section(grid, row, "显示")
        row += 1
        bold = Gtk.Switch()
        bold.set_active(bool(self.config.get("appearance.allow_bold")))
        bold.connect("notify::active", self._on_bold)
        row = self._add_row(grid, row, "允许粗体", bold)

        bright = Gtk.Switch()
        bright.set_active(bool(self.config.get("appearance.bold_is_bright")))
        bright.connect("notify::active", self._on_bright)
        row = self._add_row(grid, row, "粗体使用亮色", bright)

        return self._wrap(grid)

    def _build_fonts_page(self) -> Gtk.Widget:
        """Font selection with a live preview and one-click font downloads."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        grid = self._grid()
        row = 0
        self._add_section(grid, row, "等宽字体")
        row += 1

        self._family_combo = Gtk.ComboBoxText()
        self._populate_families()
        self._family_combo.connect("changed", self._on_family_changed)
        row = self._add_row(grid, row, "字体", self._family_combo)

        self._refresh_fonts_button = Gtk.Button(label="重新扫描")
        self._refresh_fonts_button.set_tooltip_text("重新读取系统已安装的字体")
        self._refresh_fonts_button.connect("clicked", lambda *_: self._rescan_fonts())
        row = self._add_row(grid, row, "", self._refresh_fonts_button)

        size = Gtk.SpinButton.new_with_range(5, 72, 0.5)
        size.set_digits(1)
        size.set_value(float(self.config.get("appearance.font_size")))
        size.connect("value-changed", self._on_font_size)
        row = self._add_row(grid, row, "字号", size)

        height_scale = Gtk.SpinButton.new_with_range(0.8, 1.6, 0.05)
        height_scale.set_digits(2)
        height_scale.set_value(float(self.config.get("appearance.cell_height_scale")))
        height_scale.connect("value-changed", self._on_height_scale)
        row = self._add_row(grid, row, "行高倍数", height_scale)

        width_scale = Gtk.SpinButton.new_with_range(0.8, 1.6, 0.05)
        width_scale.set_digits(2)
        width_scale.set_value(float(self.config.get("appearance.cell_width_scale")))
        width_scale.connect("value-changed", self._on_width_scale)
        row = self._add_row(grid, row, "字宽倍数", width_scale)

        self._add_section(grid, row, "中英文混排")
        row += 1
        fallback = Gtk.Entry()
        fallback.set_text(str(self.config.get("appearance.fallback_font")))
        fallback.set_placeholder_text("例如 Noto Sans Mono CJK SC")
        fallback.connect("changed", self._on_fallback_font)
        row = self._add_row(grid, row, "中文回退字体", fallback)

        hint = Gtk.Label(
            label=(
                "Vela 会把上面两个字体拼成回退列表交给系统：英文用主字体，"
                "中文自动落到回退字体，因此不必为了中文放弃编程字体。"
            )
        )
        hint.set_xalign(0.0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("vela-dim")
        row = self._add_row(grid, row, "", hint)

        self._add_section(grid, row, "预览")
        row += 1
        self._preview = Gtk.Label()
        self._preview_provider = None
        self._preview.set_xalign(0.0)
        self._preview.get_style_context().add_class("vela-font-preview")
        self._preview.set_line_wrap(True)
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        preview_box.pack_start(self._preview, False, False, 0)
        row = self._add_row(grid, row, "", preview_box)

        self._add_section(grid, row, "下载编程字体")
        row += 1
        download_hint = Gtk.Label(
            label=(
                "这些字体不在系统里，需要联网下载，装到 "
                "~/.local/share/fonts 后立即可用。"
            )
        )
        download_hint.set_xalign(0.0)
        download_hint.set_line_wrap(True)
        download_hint.get_style_context().add_class("vela-dim")
        row = self._add_row(grid, row, "", download_hint)

        self._download_buttons: Dict[str, Gtk.Button] = {}
        for candidate in fonts_mod.catalog():
            button = Gtk.Button(label=f"下载 {candidate.name}")
            button.set_tooltip_text(
                f"{candidate.description}（{candidate.licence}）"
            )
            button.connect(
                "clicked", lambda _b, key=candidate.key: self._download_font(key)
            )
            # Natural width: stretched to the column these look like text fields
            # rather than buttons.
            button.set_halign(Gtk.Align.START)
            self._download_buttons[candidate.key] = button
            row = self._add_row(grid, row, candidate.name, button)

        self._font_status = Gtk.Label(label="")
        self._font_status.set_xalign(0.0)
        self._font_status.set_line_wrap(True)
        self._font_status.get_style_context().add_class("vela-dim")
        row = self._add_row(grid, row, "", self._font_status)

        outer.pack_start(self._wrap(grid), True, True, 0)
        self._update_font_status()
        self._update_preview()
        return outer

    def _populate_families(self) -> None:
        """Fill the font combo from the fonts actually installed."""
        combo = self._family_combo
        combo.remove_all()
        current = str(self.config.get("appearance.font_family"))
        families = fonts_mod.monospace_families()
        for family in families:
            combo.append_text(family)
        # Keep a configured font selectable even if it is not monospace-detected,
        # otherwise opening the dialog would silently change the setting.
        if current and current not in families:
            combo.append_text(current)
        # append_text() does not assign ids, so set_active_id() would silently
        # fail here; select by position instead.
        if current and current in families:
            combo.set_active(families.index(current))
        elif current:
            combo.set_active(len(families))
        elif families:
            combo.set_active(0)

    def _rescan_fonts(self) -> None:
        self._populate_families()
        self._update_font_status()
        self.parent_window.show_status("已重新扫描系统字体", 3)

    def _refresh_font_summary(self) -> None:
        """One-line description of the active font, shown on the 外观 page."""
        label = getattr(self, "_font_summary", None)
        if label is None:
            return
        family = str(self.config.get("appearance.font_family") or "系统默认")
        size = float(self.config.get("appearance.font_size"))
        fallback = str(self.config.get("appearance.fallback_font") or "")
        text = f"{family} {size:g}"
        if self.config.get("appearance.use_system_font"):
            text = f"跟随系统字体（{size:g}）"
        elif fallback:
            text += f"　中文回退：{fallback}"
        label.set_text(text)
        label.set_tooltip_text(text)

    def _goto_fonts_page(self) -> None:
        """Jump to the dedicated font page."""
        notebook = self.get_content_area().get_children()[0]
        if isinstance(notebook, Gtk.Notebook):
            for index in range(notebook.get_n_pages()):
                label = notebook.get_tab_label(notebook.get_nth_page(index))
                if isinstance(label, Gtk.Label) and label.get_text() == "字体":
                    notebook.set_current_page(index)
                    return

    def _update_font_status(self) -> None:
        families = fonts_mod.monospace_families()
        installed = fonts_mod.installed_catalog_keys()
        for key, button in self._download_buttons.items():
            if key in installed:
                button.set_label("已安装")
                button.set_sensitive(False)
            else:
                entry = fonts_mod.find_candidate(key)
                button.set_label(f"下载 {entry.name}" if entry else "下载")
                button.set_sensitive(True)
        self._font_status.set_text(
            f"系统检测到 {len(families)} 个等宽字体；"
            f"已从本页安装 {len(installed)} 个编程字体。"
        )

    def _update_preview(self) -> None:
        family = str(self.config.get("appearance.font_family"))
        fallback = str(self.config.get("appearance.fallback_font") or "")
        size = float(self.config.get("appearance.font_size"))
        # Styled through CSS rather than the deprecated override_font(), which
        # newer GTK releases ignore.
        families = family if not fallback else f"{family}, {fallback}"
        provider = Gtk.CssProvider()
        provider.load_from_data(
            (
                ".vela-font-preview {"
                f"  font-family: {_css_family(families)};"
                f"  font-size: {size:g}pt;"
                "}"
            ).encode("utf-8")
        )
        context = self._preview.get_style_context()
        if self._preview_provider is not None:
            context.remove_provider(self._preview_provider)
        context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._preview_provider = provider
        self._preview.set_text(
            "0O 1lI 5S 8B — 等宽字体预览 ABCabc 0123456789 (){}[]\n"
            f"主字体：{family or '系统默认'}"
            + (f"　回退：{fallback}" if fallback else "")
        )

    def _on_family_changed(self, combo: Gtk.ComboBoxText) -> None:
        family = combo.get_active_text()
        if not family:
            return
        self.config.set("appearance.font_family", family)
        self.config.set("appearance.use_system_font", False)
        self._apply()
        self._update_preview()

    def _on_height_scale(self, spin: Gtk.SpinButton) -> None:
        self.config.set("appearance.cell_height_scale", float(spin.get_value()))
        self._apply()

    def _on_width_scale(self, spin: Gtk.SpinButton) -> None:
        self.config.set("appearance.cell_width_scale", float(spin.get_value()))
        self._apply()

    def _on_fallback_font(self, entry: Gtk.Entry) -> None:
        self.config.set("appearance.fallback_font", entry.get_text().strip())
        self._apply()
        self._update_preview()

    def _download_font(self, key: str) -> None:
        """Download a font in a worker thread so the dialog stays responsive."""
        entry = fonts_mod.find_candidate(key)
        if entry is None:
            return
        button = self._download_buttons.get(key)
        if button is not None:
            button.set_sensitive(False)
            button.set_label("下载中…")
        self._font_status.set_text(f"正在下载 {entry.name}…")

        def work() -> None:
            try:
                installed = fonts_mod.install(key)
            except fonts_mod.DownloadError as error:
                GLib.idle_add(self._on_download_failed, key, str(error))
                return
            except Exception as error:  # pragma: no cover - unexpected
                GLib.idle_add(self._on_download_failed, key, repr(error))
                return
            GLib.idle_add(self._on_download_done, key, len(installed))

        threading.Thread(target=work, daemon=True).start()

    def _on_download_done(self, key: str, count: int) -> bool:
        entry = fonts_mod.find_candidate(key)
        name = entry.name if entry else key
        self._font_status.set_text(f"{name} 安装完成（{count} 个字体文件），可立即选用。")
        self._populate_families()
        self._update_font_status()
        self.parent_window.show_status(f"{name} 已安装", 4)
        return False

    def _on_download_failed(self, key: str, message: str) -> bool:
        entry = fonts_mod.find_candidate(key)
        name = entry.name if entry else key
        self._font_status.set_text(f"{name} 下载失败：{message}")
        self._update_font_status()
        self.parent_window.show_status(f"{name} 下载失败：{message}", 8, "error")
        return False

    def _build_behavior(self) -> Gtk.Widget:
        grid = self._grid()
        row = 0

        self._add_section(grid, row, "Shell")
        row += 1
        shell = Gtk.Entry()
        shell.set_text(str(self.config.get("behavior.shell")))
        shell.set_placeholder_text("留空使用 $SHELL")
        shell.connect("changed", self._on_shell)
        row = self._add_row(grid, row, "自定义 shell", shell)

        login = Gtk.Switch()
        login.set_active(bool(self.config.get("behavior.login_shell")))
        login.connect("notify::active", self._on_login_shell)
        row = self._add_row(grid, row, "作为登录 shell 启动", login)

        cwd = Gtk.Entry()
        cwd.set_text(str(self.config.get("behavior.working_directory")))
        cwd.set_placeholder_text("留空使用当前工作目录")
        cwd.connect("changed", self._on_cwd)
        row = self._add_row(grid, row, "启动目录", cwd)

        self._add_section(grid, row, "交互")
        row += 1
        for key, label in (
            ("close_pane_on_exit", "输入 exit 后关闭该分屏"),
            ("close_window_on_last_exit", "最后一个分屏退出时关闭窗口"),
            ("close_on_abnormal_exit", "非 0 退出码也关闭（默认保留以便查看错误）"),
        ):
            switch = Gtk.Switch()
            switch.set_active(bool(self.config.get(f"behavior.{key}")))
            switch.connect("notify::active", self._on_behavior_switch, key)
            row = self._add_row(grid, row, label, switch)
        for key, label in (
            ("copy_on_select", "选中即复制"),
            ("confirm_close", "关闭前确认运行中的进程"),
            ("scroll_on_keystroke", "输入时滚动到底部"),
            ("scroll_on_output", "有输出时滚动到底部"),
            ("audible_bell", "响铃声音"),
            ("visual_bell", "响铃闪烁"),
            ("allow_hyperlink", "识别超链接（Ctrl+点击打开）"),
        ):
            switch = Gtk.Switch()
            switch.set_active(bool(self.config.get(f"behavior.{key}")))
            switch.connect("notify::active", self._on_behavior_switch, key)
            row = self._add_row(grid, row, label, switch)

        word_chars = Gtk.Entry()
        word_chars.set_text(str(self.config.get("behavior.word_char_exceptions")))
        word_chars.connect("changed", self._on_word_chars)
        row = self._add_row(grid, row, "单词分隔例外字符", word_chars)

        return self._wrap(grid)

    def _build_window_page(self) -> Gtk.Widget:
        grid = self._grid()
        row = 0

        self._add_section(grid, row, "窗口")
        row += 1
        show_header = Gtk.Switch()
        show_header.set_active(bool(self.config.get("window.show_headerbar")))
        show_header.connect("notify::active", self._on_window_switch, "show_headerbar")
        row = self._add_row(grid, row, "显示标题栏", show_header)

        show_status = Gtk.Switch()
        show_status.set_active(bool(self.config.get("window.show_statusbar")))
        show_status.connect("notify::active", self._on_window_switch, "show_statusbar")
        row = self._add_row(grid, row, "显示状态栏", show_status)

        tabbar = Gtk.ComboBoxText()
        for value, label in (
            ("always", "始终显示"),
            ("multiple", "多个标签时显示"),
            ("never", "从不显示"),
        ):
            tabbar.append(value, label)
        tabbar.set_active_id(str(self.config.get("window.show_tabbar")))
        tabbar.connect("changed", self._on_tabbar_mode)
        row = self._add_row(grid, row, "标签栏", tabbar)

        position = Gtk.ComboBoxText()
        for value, label in (("top", "顶部"), ("bottom", "底部")):
            position.append(value, label)
        position.set_active_id(str(self.config.get("window.tab_position")))
        position.connect("changed", self._on_tab_position)
        row = self._add_row(grid, row, "标签栏位置", position)

        sidebar_width = Gtk.SpinButton.new_with_range(200, 900, 10)
        sidebar_width.set_value(float(self.config.get("window.sidebar_width")))
        sidebar_width.connect("value-changed", self._on_sidebar_width)
        row = self._add_row(grid, row, "侧栏宽度（文件 / 性能面板共用）", sidebar_width)

        restore = Gtk.Switch()
        restore.set_active(bool(self.config.get("window.restore_working_directory")))
        restore.connect(
            "notify::active", self._on_window_switch, "restore_working_directory"
        )
        row = self._add_row(grid, row, "分屏时继承当前目录", restore)

        self._add_section(grid, row, "配置文件")
        row += 1
        path_label = Gtk.Label(label=self.config.path)
        path_label.set_xalign(0.0)
        path_label.set_selectable(True)
        path_label.set_ellipsize(3)
        row = self._add_row(grid, row, "路径", path_label)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        open_button = Gtk.Button(label="重新加载配置")
        open_button.connect("clicked", lambda *_: self._reload())
        save_button = Gtk.Button(label="写入配置文件")
        save_button.connect("clicked", lambda *_: self._save())
        button_box.pack_start(open_button, False, False, 0)
        button_box.pack_start(save_button, False, False, 0)
        row = self._add_row(grid, row, "", button_box)

        return self._wrap(grid)

    def _build_shortcuts(self) -> Gtk.Widget:
        store = Gtk.ListStore(str, str, str)
        for action, accelerator in sorted(self.config.keybindings().items()):
            store.append([action, accelerator, keymap.display(accelerator)])
        view = Gtk.TreeView(model=store)
        for title, index in (("动作", 0), ("快捷键", 2)):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_expand(index == 0)
            view.append_column(column)

        hint = Gtk.Label(
            label="快捷键可在 config.toml 的 [keybindings] 段中修改，保存后重启或重新加载配置生效。"
        )
        hint.set_xalign(0.0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("vela-dim")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(view)
        box.pack_start(hint, False, False, 0)
        box.pack_start(scroller, True, True, 0)
        return box

    def _build_panels_page(self) -> Gtk.Widget:
        grid = self._grid()
        row = 0

        self._add_section(grid, row, "系统性能面板")
        row += 1
        enabled = Gtk.Switch()
        enabled.set_active(bool(self.config.get("sysinfo.enabled")))
        enabled.connect("notify::active", self._on_sysinfo_switch, "enabled")
        row = self._add_row(grid, row, "显示面板", enabled)

        interval = Gtk.SpinButton.new_with_range(0.5, 60.0, 0.5)
        interval.set_digits(1)
        interval.set_value(float(self.config.get("sysinfo.interval")))
        interval.connect("value-changed", self._on_sysinfo_interval)
        row = self._add_row(grid, row, "刷新间隔（秒）", interval)

        loopback = Gtk.Switch()
        loopback.set_active(bool(self.config.get("sysinfo.hide_loopback")))
        loopback.connect("notify::active", self._on_sysinfo_switch, "hide_loopback")
        row = self._add_row(grid, row, "隐藏回环网卡", loopback)

        interfaces = Gtk.SpinButton.new_with_range(1, 16, 1)
        interfaces.set_value(float(self.config.get("sysinfo.max_interfaces")))
        interfaces.connect("value-changed", self._on_sysinfo_interfaces)
        row = self._add_row(grid, row, "显示网卡数量", interfaces)

        in_status = Gtk.Switch()
        in_status.set_active(bool(self.config.get("sysinfo.show_in_statusbar")))
        in_status.connect("notify::active", self._on_sysinfo_switch, "show_in_statusbar")
        row = self._add_row(grid, row, "状态栏显示 CPU/内存", in_status)

        self._add_section(grid, row, "文件打开")
        row += 1
        browser = Gtk.Switch()
        browser.set_active(bool(self.config.get("filebrowser.enabled")))
        browser.connect("notify::active", self._on_browser_switch, "enabled")
        row = self._add_row(grid, row, "显示文件面板", browser)

        follow = Gtk.Switch()
        follow.set_active(bool(self.config.get("filebrowser.follow_terminal")))
        follow.connect("notify::active", self._on_browser_switch, "follow_terminal")
        row = self._add_row(grid, row, "文件面板跟随终端目录", follow)

        hidden = Gtk.Switch()
        hidden.set_active(bool(self.config.get("filebrowser.show_hidden")))
        hidden.connect("notify::active", self._on_browser_switch, "show_hidden")
        row = self._add_row(grid, row, "显示隐藏文件", hidden)

        max_entries = Gtk.SpinButton.new_with_range(100, 200000, 500)
        max_entries.set_value(float(self.config.get("filebrowser.max_entries")))
        max_entries.connect("value-changed", self._on_browser_max_entries)
        row = self._add_row(grid, row, "单目录最多列出", max_entries)

        for key, label in (
            ("open_internally", "文本与图片用内置查看器打开"),
            ("ctrl_click_paths", "Ctrl+点击打开路径"),
            ("reveal_on_ctrl_shift_click", "Ctrl+Shift+点击在文件管理器中显示"),
        ):
            switch = Gtk.Switch()
            switch.set_active(bool(self.config.get(f"files.{key}")))
            switch.connect("notify::active", self._on_files_switch, key)
            row = self._add_row(grid, row, label, switch)

        hint = Gtk.Label(
            label="提示：Ctrl+Shift+Enter 打开光标附近的路径，Ctrl+Enter 打开选中的路径。"
        )
        hint.set_xalign(0.0)
        hint.set_line_wrap(True)
        hint.get_style_context().add_class("vela-dim")
        row = self._add_row(grid, row, "", hint)
        return self._wrap(grid)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _grid(self) -> Gtk.Grid:
        grid = Gtk.Grid()
        grid.set_row_spacing(6)
        grid.set_column_spacing(12)
        grid.set_border_width(14)
        # Let the label column shrink and the control column absorb the extra
        # width; otherwise a wide control stretches column 1 and the labels in
        # column 0 get pushed out of view.
        grid.set_column_homogeneous(False)
        return grid

    def _wrap(self, grid: Gtk.Grid) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("vela-scroll")
        scroller.add(grid)
        return scroller

    def _add_section(self, grid: Gtk.Grid, row: int, title: str) -> None:
        label = Gtk.Label(label=title)
        label.set_xalign(0.0)
        label.get_style_context().add_class("vela-section-title")
        grid.attach(label, 0, row, 2, 1)

    def _add_row(self, grid: Gtk.Grid, row: int, label: str, widget: Gtk.Widget) -> int:
        if label:
            text = Gtk.Label(label=label)
            text.set_xalign(0.0)
            grid.attach(text, 0, row, 1, 1)
            # Switches and buttons keep their natural size; stretching a switch
            # turns it into an oversized ellipse instead of a toggle.
            if isinstance(widget, (Gtk.Switch, Gtk.Button, Gtk.FontButton)):
                widget.set_halign(Gtk.Align.START)
            else:
                widget.set_hexpand(True)
            grid.attach(widget, 1, row, 1, 1)
        else:
            # A full-width row: constrain wrapping labels to the column width so
            # a long hint cannot force the whole page wider than the dialog
            # (which pushed the left-hand labels off-screen).
            if isinstance(widget, Gtk.Label):
                widget.set_line_wrap(True)
                widget.set_max_width_chars(48)
                widget.set_xalign(0.0)
            elif isinstance(widget, Gtk.Box):
                for child in widget.get_children():
                    if isinstance(child, Gtk.Label):
                        child.set_line_wrap(True)
                        child.set_max_width_chars(48)
            grid.attach(widget, 0, row, 2, 1)
        return row + 1

    def _apply(self) -> None:
        if self._suspend:
            return
        window = self.parent_window
        window.theme = self.config.theme()
        window._refresh_css()
        for tab in window.tabs:
            tab.apply_theme(window.theme)
        # Font, cursor, padding and scrollback changes need this too; without it
        # changing the font family in this dialog had no visible effect.
        window._apply_appearance()
        window.apply_window_options()
        self._refresh_font_summary()
        self._dirty = True

    # ------------------------------------------------------------------
    # callbacks
    # ------------------------------------------------------------------
    def _on_theme_changed(self, combo: Gtk.ComboBoxText) -> None:
        name = combo.get_active_id()
        if name:
            self.config.set("appearance.theme", name)
            self._apply()

    def _on_font_size(self, spin: Gtk.SpinButton) -> None:
        self.config.set("appearance.font_size", float(spin.get_value()))
        self._apply()

    def _on_system_font(self, switch: Gtk.Switch, _param) -> None:
        self.config.set("appearance.use_system_font", bool(switch.get_active()))
        self._apply()

    def _on_cursor_shape(self, combo: Gtk.ComboBoxText) -> None:
        if combo.get_active_id():
            self.config.set("appearance.cursor_shape", combo.get_active_id())
            self._apply()

    def _on_cursor_blink(self, combo: Gtk.ComboBoxText) -> None:
        if combo.get_active_id():
            self.config.set("appearance.cursor_blink", combo.get_active_id())
            self._apply()

    def _on_scrollback(self, spin: Gtk.SpinButton) -> None:
        self.config.set("appearance.scrollback_lines", int(spin.get_value()))
        self._apply()

    def _on_padding(self, spin: Gtk.SpinButton) -> None:
        self.config.set("appearance.padding", int(spin.get_value()))
        self._apply()

    def _on_bold(self, switch: Gtk.Switch, _param) -> None:
        self.config.set("appearance.allow_bold", bool(switch.get_active()))
        self._apply()

    def _on_bright(self, switch: Gtk.Switch, _param) -> None:
        self.config.set("appearance.bold_is_bright", bool(switch.get_active()))
        self._apply()

    def _on_shell(self, entry: Gtk.Entry) -> None:
        self.config.set("behavior.shell", entry.get_text())
        self._dirty = True

    def _on_login_shell(self, switch: Gtk.Switch, _param) -> None:
        self.config.set("behavior.login_shell", bool(switch.get_active()))
        self._dirty = True

    def _on_cwd(self, entry: Gtk.Entry) -> None:
        self.config.set("behavior.working_directory", entry.get_text())
        self._dirty = True

    def _on_word_chars(self, entry: Gtk.Entry) -> None:
        self.config.set("behavior.word_char_exceptions", entry.get_text())
        self._apply()

    def _on_behavior_switch(self, switch: Gtk.Switch, _param, key: str) -> None:
        self.config.set(f"behavior.{key}", bool(switch.get_active()))
        self._apply()

    def _on_window_switch(self, switch: Gtk.Switch, _param, key: str) -> None:
        self.config.set(f"window.{key}", bool(switch.get_active()))
        self._apply()

    def _on_tabbar_mode(self, combo: Gtk.ComboBoxText) -> None:
        if combo.get_active_id():
            self.config.set("window.show_tabbar", combo.get_active_id())
            self._apply()

    def _on_tab_position(self, combo: Gtk.ComboBoxText) -> None:
        if combo.get_active_id():
            self.config.set("window.tab_position", combo.get_active_id())
            self._apply()

    def _on_sidebar_width(self, spin: Gtk.SpinButton) -> None:
        self.config.set("window.sidebar_width", int(spin.get_value()))
        self._apply()

    def _on_sysinfo_switch(self, switch: Gtk.Switch, _param, key: str) -> None:
        self.config.set(f"sysinfo.{key}", bool(switch.get_active()))
        self._apply()
        if key == "enabled":
            self.parent_window.set_sysinfo_visible(
                bool(switch.get_active()), persist=False
            )

    def _on_sysinfo_interval(self, spin: Gtk.SpinButton) -> None:
        self.config.set("sysinfo.interval", float(spin.get_value()))
        self._apply()

    def _on_sysinfo_interfaces(self, spin: Gtk.SpinButton) -> None:
        self.config.set("sysinfo.max_interfaces", int(spin.get_value()))
        self._apply()

    def _on_files_switch(self, switch: Gtk.Switch, _param, key: str) -> None:
        self.config.set(f"files.{key}", bool(switch.get_active()))
        self._apply()

    def _on_browser_switch(self, switch: Gtk.Switch, _param, key: str) -> None:
        self.config.set(f"filebrowser.{key}", bool(switch.get_active()))
        self._apply()
        window = self.parent_window
        if key == "enabled":
            window.set_filebrowser_visible(bool(switch.get_active()), persist=False)
        elif key == "follow_terminal" and window.filebrowser_panel is not None:
            window.filebrowser_panel.following = bool(switch.get_active())
            window.filebrowser_panel._sync_toolbar()
        elif key == "show_hidden" and window.filebrowser_panel is not None:
            panel = window.filebrowser_panel
            if panel.show_hidden != bool(switch.get_active()):
                panel.toggle_hidden()

    def _on_browser_max_entries(self, spin: Gtk.SpinButton) -> None:
        self.config.set("filebrowser.max_entries", int(spin.get_value()))
        panel = self.parent_window.filebrowser_panel
        if panel is not None:
            panel.refresh()

    def _reload(self) -> None:
        self.parent_window.reload_config()
        self._dirty = False
        self.destroy()
        self.parent_window.open_preferences()

    def _save(self) -> None:
        self.parent_window._save_config()
        self._dirty = False
        self.parent_window.show_status("配置已写入 " + self.config.path, 4)
