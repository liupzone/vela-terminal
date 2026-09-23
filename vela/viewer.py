"""Built-in file viewer: syntax-highlighted text and image preview.

Text uses GtkSourceView (shipped with Ubuntu's desktop, no extra install) so
opening a config, log or source file from the terminal gets highlighting and
line numbers for free.  Images use GdkPixbuf with a zoom-to-fit preview.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkSource", "4")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkSource, Pango  # noqa: E402

from . import files, theme as theme_mod  # noqa: E402
from .sysinfo import format_bytes as format_size  # noqa: E402

_SCHEME_FOR_THEME = {
    "vela-dark": "oblivion",
    "vela-light": "classic",
    "dracula": "oblivion",
    "nord": "oblivion",
    "tokyo-night": "oblivion",
    "catppuccin-mocha": "oblivion",
    "gruvbox-dark": "oblivion",
    "solarized-dark": "solarized-dark",
    "solarized-light": "solarized-light",
    "one-dark": "oblivion",
}


class TextViewer(Gtk.Box):
    """Editable, syntax-highlighted text view with search and save."""

    def __init__(self, path: str, theme: theme_mod.Theme) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.path = path
        self.theme = theme
        self._modified = False
        self._mtime: Optional[float] = None
        self._on_status: Optional[Callable[[str], None]] = None

        self.buffer = GtkSource.Buffer()
        self._apply_language()
        self.view = GtkSource.View.new_with_buffer(self.buffer)
        self.view.set_show_line_numbers(True)
        # GtkSourceView's "current-line" style is authored for its own light
        # style schemes, and on a dark theme it paints a bright band across the
        # caret line that clashes with the window colours.  The caret is already
        # visible, so the band is left off.
        self.view.set_highlight_current_line(False)
        self.view.set_auto_indent(True)
        self.view.set_tab_width(4)
        self.view.set_insert_spaces_instead_of_tabs(True)
        self.view.set_monospace(True)
        self.view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.view.get_style_context().add_class("vela-source")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("vela-scroll")
        scroller.add(self.view)
        self.pack_start(scroller, True, True, 0)

        self.buffer.connect("changed", self._on_changed)
        self.buffer.connect("mark-set", self._on_mark_set)
        self._position = Gtk.Label(label="")
        self._position.get_style_context().add_class("vela-dim")

    # -- api -------------------------------------------------------------
    def load(self) -> None:
        text = files.read_text(self.path)
        self.buffer.set_text(text)
        self.buffer.set_modified(False)
        self._modified = False
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:
            self._mtime = None
        self.view.grab_focus()

    @property
    def modified(self) -> bool:
        return self._modified

    def save(self) -> bool:
        if self._changed_on_disk():
            return False
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        files.write_text(self.path, text)
        self.buffer.set_modified(False)
        self._modified = False
        try:
            self._mtime = os.path.getmtime(self.path)
        except OSError:
            self._mtime = None
        return True

    def _changed_on_disk(self) -> bool:
        if self._mtime is None:
            return False
        try:
            return abs(os.path.getmtime(self.path) - self._mtime) > 1e-6
        except OSError:
            return True

    def reload_from_disk(self) -> None:
        self.load()

    def find(self, pattern: str, forward: bool = True) -> bool:
        if not pattern:
            return False
        match = None
        if forward:
            start = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            match = start.forward_search(
                pattern, Gtk.TextSearchFlags.CASE_INSENSITIVE, None
            )
            if match is None:
                match = self.buffer.get_start_iter().forward_search(
                    pattern, Gtk.TextSearchFlags.CASE_INSENSITIVE, None
                )
        else:
            start = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            match = start.backward_search(
                pattern, Gtk.TextSearchFlags.CASE_INSENSITIVE, None
            )
            if match is None:
                match = self.buffer.get_end_iter().backward_search(
                    pattern, Gtk.TextSearchFlags.CASE_INSENSITIVE, None
                )
        if match is None:
            return False
        self.buffer.select_range(match[0], match[1])
        self.view.scroll_to_iter(match[0], 0.15, False, 0.0, 0.5)
        return True

    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        manager = GtkSource.StyleSchemeManager.get_default()
        name = _SCHEME_FOR_THEME.get(theme.name, "classic")
        scheme = manager.get_scheme(name)
        if scheme is not None:
            self.buffer.set_style_scheme(scheme)

    def position_text(self) -> str:
        return self._position.get_text()

    @property
    def status_widget(self) -> Gtk.Widget:
        return self._position

    # -- internals -------------------------------------------------------
    def _apply_language(self) -> None:
        manager = GtkSource.LanguageManager.get_default()
        language = manager.guess_language(os.path.basename(self.path), None)
        if language is not None:
            self.buffer.set_language(language)

    def _on_changed(self, _buffer) -> None:
        modified = self.buffer.get_modified()
        if modified != self._modified:
            self._modified = modified
            if self._on_status is not None:
                self._on_status("已修改" if modified else "未修改")

    def _on_mark_set(self, _buffer, _iterator, mark) -> None:
        if mark.get_name() != "insert":
            return
        line, column = self.buffer.get_iter_at_mark(mark).get_line(), None
        column = self.buffer.get_iter_at_mark(mark).get_line_offset()
        self._position.set_text(f"行 {line + 1} 列 {column + 1}")


class ImageViewer(Gtk.Box):
    """Image preview with zoom-to-fit and a scale readout."""

    def __init__(self, path: str, theme: theme_mod.Theme) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.path = path
        self.theme = theme
        self._pixbuf: Optional[GdkPixbuf.Pixbuf] = None
        self._fit = True

        self.image = Gtk.Image()
        self.image.get_style_context().add_class("vela-image")
        self.image.set_halign(Gtk.Align.CENTER)
        self.image.set_valign(Gtk.Align.CENTER)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("vela-scroll")
        scroller.add(self.image)
        self.pack_start(scroller, True, True, 0)
        self._scroller = scroller

        self._info = Gtk.Label(label="")
        self._info.get_style_context().add_class("vela-dim")

    def load(self) -> None:
        self._pixbuf = GdkPixbuf.Pixbuf.new_from_file(self.path)
        self.apply_scale()

    def apply_scale(self) -> None:
        if self._pixbuf is None:
            return
        width, height = self._pixbuf.get_width(), self._pixbuf.get_height()
        if self._fit:
            available = max(80, self._scroller.get_allocated_width() - 24)
            available_height = max(80, self._scroller.get_allocated_height() - 24)
            scale = min(1.0, available / max(1, width), available_height / max(1, height))
            target = (max(1, int(width * scale)), max(1, int(height * scale)))
        else:
            scale = 1.0
            target = (width, height)
        pixbuf = self._pixbuf.scale_simple(
            target[0], target[1], GdkPixbuf.InterpType.BILINEAR
        )
        self.image.set_from_pixbuf(pixbuf)
        self._info.set_text(
            f"{width}×{height} · 显示 {int(scale * 100)}% · "
            f"{format_size(os.path.getsize(self.path))}"
        )

    def set_fit(self, fit: bool) -> None:
        self._fit = fit
        self.apply_scale()

    def zoom(self, factor: float) -> None:
        self._fit = False
        if self._pixbuf is None:
            return
        current = self.image.get_pixbuf()
        width = max(16, int((current.get_width() if current else self._pixbuf.get_width()) * factor))
        height = max(16, int((current.get_height() if current else self._pixbuf.get_height()) * factor))
        self.image.set_from_pixbuf(
            self._pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        )
        self._info.set_text(f"显示 {width}×{height}")

    def status_text(self) -> str:
        return self._info.get_text()

    @property
    def status_widget(self) -> Gtk.Widget:
        return self._info

    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme


class FileViewerWindow(Gtk.Window):
    """Standalone viewer window opened from the terminal."""

    def __init__(
        self,
        parent: Optional[Gtk.Window],
        path: str,
        target: files.Target,
        theme: theme_mod.Theme,
        notify: Optional[Callable[[str, float, str], None]] = None,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL, transient_for=parent)
        self.path = target.path
        self.target = target
        self.theme = theme
        self._notify = notify or (lambda *_args: None)
        self.get_style_context().add_class("vela")

        if target.kind == files.KIND_IMAGE:
            self.viewer = ImageViewer(target.path, theme)
        else:
            self.viewer = TextViewer(target.path, theme)

        self.headerbar = Gtk.HeaderBar()
        self.headerbar.get_style_context().add_class("vela-headerbar")
        self.headerbar.set_show_close_button(True)
        self.headerbar.set_title(os.path.basename(target.path) or target.path)
        self.headerbar.set_subtitle(target.path)
        self.set_titlebar(self.headerbar)

        self._build_actions()
        self.set_default_size(900, 660)
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)
        self._load()
        self.apply_theme(theme)

    # -- construction ----------------------------------------------------
    def _build_actions(self) -> None:
        self._buttons = {}
        if isinstance(self.viewer, TextViewer):
            specs = [
                ("保存", "document-save-symbolic", self.save),
                ("重新加载", "view-refresh-symbolic", self.reload),
            ]
        else:
            specs = [
                ("适应窗口", "zoom-fit-best-symbolic", lambda: self.viewer.set_fit(True)),
                ("放大", "zoom-in-symbolic", lambda: self.viewer.zoom(1.25)),
                ("缩小", "zoom-out-symbolic", lambda: self.viewer.zoom(0.8)),
            ]
        for label, icon, callback in specs:
            button = Gtk.Button()
            button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
            button.set_tooltip_text(label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.connect("clicked", lambda *_args, cb=callback: cb())
            self.headerbar.pack_start(button)
            self._buttons[label] = button

        open_button = Gtk.Button()
        open_button.set_image(
            Gtk.Image.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.MENU)
        )
        open_button.set_tooltip_text("用系统默认程序打开")
        open_button.set_relief(Gtk.ReliefStyle.NONE)
        open_button.connect("clicked", lambda *_: self.open_externally())
        self.headerbar.pack_end(open_button)

        self._status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self._status.get_style_context().add_class("vela-statusbar")
        widget = getattr(self.viewer, "status_widget", None)
        if widget is not None:
            self._status.pack_start(widget, False, False, 0)
        self._status_label = Gtk.Label(label="")
        self._status_label.get_style_context().add_class("vela-dim")
        self._status.pack_end(self._status_label, False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.pack_start(self.viewer, True, True, 0)
        body.pack_start(self._status, False, False, 0)
        self.add(body)

    # -- api -------------------------------------------------------------
    def _load(self) -> None:
        try:
            self.viewer.load()
        except (OSError, ValueError, GLib.Error) as error:
            self._notify(f"无法打开 {os.path.basename(self.path)}：{error}", 8.0, "error")
            self._status_label.set_text(f"打开失败：{error}")
            return
        if isinstance(self.viewer, TextViewer):
            self._status_label.set_text("只读预览可编辑，Ctrl+S 保存")
        else:
            self._status_label.set_text(self.viewer.status_text())

    def save(self) -> bool:
        if not isinstance(self.viewer, TextViewer):
            return False
        if not self.viewer.modified:
            self._notify("没有需要保存的修改", 3.0)
            return False
        if not self.viewer.save():
            self._notify("文件已被外部修改，未保存以免覆盖", 8.0, "error")
            return False
        self._notify(f"已保存 {os.path.basename(self.path)}", 3.0)
        return True

    def reload(self) -> None:
        if isinstance(self.viewer, TextViewer) and self.viewer.modified:
            self._notify("有未保存的修改，已取消重新加载", 5.0, "warning")
            return
        self._load()

    def open_externally(self) -> None:
        if files.open_with_desktop(self.path):
            self._notify("已交给系统默认程序", 3.0)
        else:
            self._notify("找不到可用的系统默认程序", 6.0, "error")

    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        self.viewer.apply_theme(theme)

    # -- signals ---------------------------------------------------------
    def _on_delete(self, *_args) -> bool:
        if isinstance(self.viewer, TextViewer) and self.viewer.modified:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text="文件有未保存的修改",
            )
            dialog.format_secondary_text("关闭窗口会丢弃这些修改。")
            dialog.add_button("取消", Gtk.ResponseType.CANCEL)
            discard = dialog.add_button("放弃修改", Gtk.ResponseType.OK)
            discard.get_style_context().add_class("destructive-action")
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return True
        return False

    def _on_key_press(self, _widget, event) -> bool:
        control = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if control and event.keyval in (ord("s"), ord("S")):
            self.save()
            return True
        if control and event.keyval in (ord("r"), ord("R")):
            self.reload()
            return True
        if control and event.keyval in (ord("q"), ord("Q")):
            self.close()
            return True
        return False
