"""Command palette: fuzzy-search every action in the app."""

from __future__ import annotations

from typing import Callable, List, Sequence, Tuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


def fuzzy_score(query: str, text: str) -> Optional[float]:
    """Subsequence match score; ``None`` when ``query`` is not a subsequence."""
    if not query:
        return 0.0
    lowered_query = query.lower()
    lowered_text = text.lower()
    position = 0
    score = 0.0
    streak = 0
    for index, char in enumerate(lowered_text):
        if position < len(lowered_query) and char == lowered_query[position]:
            streak += 1
            score += 8 + streak * 4
            if index == 0:
                score += 12
            position += 1
        else:
            streak = 0
    if position != len(lowered_query):
        return None
    score -= len(lowered_text) * 0.15
    return score


class CommandPalette(Gtk.Box):
    """Overlay widget listing commands, themes and keybindings."""

    def __init__(self, on_run: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._on_run = on_run
        self._entries: List[Tuple[str, str, str, str]] = []
        self.get_style_context().add_class("vela-palette")

        self.entry = Gtk.Entry()
        self.entry.get_style_context().add_class("vela-palette-entry")
        self.entry.set_placeholder_text("输入命令、主题或快捷键…")
        self.entry.connect("changed", lambda *_: self._refresh())
        self.entry.connect("key-press-event", self._on_key_press)
        self.pack_start(self.entry, False, False, 0)

        self._store = Gtk.ListStore(str, str, str)
        self.view = Gtk.TreeView(model=self._store)
        self.view.set_headers_visible(False)
        self.view.set_enable_search(False)
        self.view.get_style_context().add_class("vela-palette-list")
        self.view.connect("row-activated", lambda *_: self.activate())
        self.view.connect("button-press-event", self._on_click)
        self.view.get_selection().set_mode(Gtk.SelectionMode.SINGLE)

        label_renderer = Gtk.CellRendererText()
        label_renderer.set_property("ellipsize", 3)
        hint_renderer = Gtk.CellRendererText()
        hint_renderer.set_property("xalign", 1.0)
        hint_renderer.set_property("scale", 0.9)
        column = Gtk.TreeViewColumn("命令", label_renderer, text=0)
        column.set_expand(True)
        hint_column = Gtk.TreeViewColumn("", hint_renderer, text=1)
        hint_column.set_alignment(1.0)
        self.view.append_column(column)
        self.view.append_column(hint_column)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(280)
        scroller.add(self.view)
        self.pack_start(scroller, True, True, 0)

        self._hint = Gtk.Label(label="↑↓ 选择 · Enter 执行 · Esc 关闭")
        self._hint.set_xalign(0.0)
        self._hint.get_style_context().add_class("vela-dim")
        self.pack_start(self._hint, False, False, 0)

    # -- api -------------------------------------------------------------
    def set_entries(self, entries: Sequence[Tuple[str, str, str, str]]) -> None:
        """``entries`` items are ``(action, label, hint, keywords)``."""
        self._entries = list(entries)

    def open(self) -> None:
        self.entry.set_text("")
        self.show_all()
        self._refresh()
        self.entry.grab_focus()

    def activate(self) -> None:
        model, tree_iter = self.view.get_selection().get_selected()
        if tree_iter is None:
            return
        action = model.get_value(tree_iter, 2)
        self.hide()
        self._on_run(action)

    # -- internals -------------------------------------------------------
    def _refresh(self) -> None:
        query = self.entry.get_text().strip()
        scored: List[Tuple[float, Tuple[str, str, str, str]]] = []
        for entry in self._entries:
            action, label, hint, keywords = entry
            haystack = " ".join([label, keywords, hint, action])
            score = fuzzy_score(query, label)
            keyword_score = fuzzy_score(query, haystack)
            if score is None and keyword_score is None:
                continue
            best = max(score or 0.0, (keyword_score or 0.0) - 6)
            scored.append((best, entry))
        scored.sort(key=lambda item: (-item[0], item[1][1]))
        self._store.clear()
        for _score, (action, label, hint, _keywords) in scored[:120]:
            self._store.append([label, hint, action])
        if len(self._store):
            self.view.get_selection().select_path(Gtk.TreePath.new_first())

    def _move(self, delta: int) -> None:
        model, tree_iter = self.view.get_selection().get_selected()
        if tree_iter is None:
            return
        path = model.get_path(tree_iter)
        index = max(0, min(len(model) - 1, path.get_indices()[0] + delta))
        self.view.get_selection().select_path(Gtk.TreePath.new_from_indices([index]))
        self.view.scroll_to_cell(Gtk.TreePath.new_from_indices([index]), None, False, 0, 0)

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == 65307:  # Escape
            self.hide()
            return True
        if event.keyval in (65362, 65364):  # Up / Down
            self._move(-1 if event.keyval == 65362 else 1)
            return True
        if event.keyval in (65293, 65421):  # Return / KP_Enter
            self.activate()
            return True
        if event.keyval == 65289:  # Tab
            self._move(1 if not (event.state & 1) else -1)
            return True
        return False

    def _on_click(self, _widget, event) -> bool:
        if event.button == 1:
            path_info = self.view.get_path_at_pos(int(event.x), int(event.y))
            if path_info:
                self.view.get_selection().select_path(path_info[0])
                self.activate()
                return True
        return False
