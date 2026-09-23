"""In-terminal search bar."""

from __future__ import annotations

from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


class SearchBar(Gtk.Box):
    """A floating search widget; call :meth:`open` to reveal it."""

    def __init__(
        self,
        on_search: Callable[[str, bool, bool], bool],
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._on_search = on_search
        self._on_close = on_close or (lambda: None)
        self.get_style_context().add_class("vela-search-box")

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("搜索…")
        self.entry.set_width_chars(26)
        self.entry.set_activates_default(False)
        self.entry.connect("activate", lambda *_: self.find_next())
        self.entry.connect("changed", lambda *_: self._run(forward=True))
        self.entry.connect("key-press-event", self._on_key_press)
        self.pack_start(self.entry, True, True, 0)

        self.regex_toggle = Gtk.ToggleButton(label=".*")
        self.regex_toggle.set_tooltip_text("使用正则表达式")
        self.regex_toggle.get_style_context().add_class("vela-icon-button")
        self.regex_toggle.connect("toggled", lambda *_: self._run(forward=True))
        self.pack_start(self.regex_toggle, False, False, 0)

        self.count_label = Gtk.Label(label="")
        self.count_label.get_style_context().add_class("vela-search-count")
        self.pack_start(self.count_label, False, False, 0)

        self.prev_button = self._icon_button("go-up-symbolic", "上一个 (Shift+Enter)", self.find_previous)
        self.next_button = self._icon_button("go-down-symbolic", "下一个 (Enter)", self.find_next)
        self.close_button = self._icon_button("window-close-symbolic", "关闭 (Esc)", self.close)
        for button in (self.prev_button, self.next_button, self.close_button):
            self.pack_start(button, False, False, 0)

    def _icon_button(self, icon: str, tooltip: str, callback: Callable) -> Gtk.Button:
        button = Gtk.Button()
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        button.set_image(image)
        button.set_tooltip_text(tooltip)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("vela-icon-button")
        button.connect("clicked", lambda *_: callback())
        return button

    # -- api -------------------------------------------------------------
    def open(self, initial: str = "") -> None:
        self.show_all()
        if initial:
            self.entry.set_text(initial)
        self.entry.grab_focus()
        self.entry.select_region(0, -1)
        self._run(forward=True)

    def close(self) -> None:
        self.hide()
        self._on_close()

    def is_open(self) -> bool:
        return self.get_visible()

    def find_next(self) -> None:
        self._run(forward=True)

    def find_previous(self) -> None:
        self._run(forward=False)

    def pattern(self) -> str:
        return self.entry.get_text()

    def is_regex(self) -> bool:
        return self.regex_toggle.get_active()

    def set_count(self, text: str) -> None:
        self.count_label.set_text(text)

    # -- internals -------------------------------------------------------
    def _run(self, forward: bool) -> bool:
        found = self._on_search(self.pattern(), self.is_regex(), forward)
        if not self.pattern():
            self.set_count("")
        else:
            self.set_count("" if found else "无结果")
        return found

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == 65307:  # Escape
            self.close()
            return True
        if event.keyval in (65293, 65421):  # Return / KP_Enter
            if event.state & 1:  # Shift
                self.find_previous()
            else:
                self.find_next()
            return True
        return False
