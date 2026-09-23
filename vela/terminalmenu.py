"""Context menu for the terminal area.

The menu is described as data (:func:`menu_items`) and turned into widgets by
:func:`build_menu`.  Keeping the description separate means the grouping,
labels, shortcut hints and enabled state can be tested without showing a menu,
and the items simply trigger the window's existing actions so the menu, the
shortcuts and the command palette can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

#: Marks a separator between groups.
SEPARATOR = None


@dataclass
class MenuItem:
    """One entry: an action name plus how it should be presented."""

    action: str
    label: str
    accel_action: str = ""
    enabled: bool = True
    hint: str = ""


def menu_items(
    has_selection: bool = False,
    pane_count: int = 1,
    zoomed: bool = False,
) -> List[Optional[MenuItem]]:
    """Describe the context menu for the current pane state.

    ``None`` entries are separators.  ``pane_count`` of 1 makes "close pane"
    behave as "close tab" (the same rule the shortcut follows), so the label
    says so.
    """
    close_label = "关闭当前分屏" if pane_count > 1 else "关闭标签页"
    return [
        MenuItem("copy", "复制选区", "copy", enabled=has_selection),
        MenuItem("paste", "粘贴", "paste"),
        MenuItem("paste-escaped", "粘贴为转义文本", "paste_escaped"),
        SEPARATOR,
        MenuItem("split-vertical", "左右分屏", "split_vertical"),
        MenuItem("split-horizontal", "上下分屏", "split_horizontal"),
        MenuItem("close-pane", close_label, "close_pane"),
        MenuItem(
            "zoom-pane",
            "还原分屏布局" if zoomed else "最大化当前分屏",
            "zoom_pane",
            enabled=pane_count > 1 or zoomed,
        ),
        SEPARATOR,
        MenuItem("select-all", "全选", "select_all"),
        MenuItem("clear", "清屏", "clear"),
        MenuItem("reset", "重置终端", "reset"),
        SEPARATOR,
        MenuItem("open-path", "打开光标附近的路径", "open_path"),
        MenuItem("open-selection", "打开选中的路径", "open_selection",
                 enabled=has_selection),
        MenuItem("reveal-path", "在文件管理器中显示当前目录", "reveal_path"),
    ]


def build_menu(
    items: Sequence[Optional[MenuItem]],
    on_activate: Callable[[str], None],
    accel_lookup: Optional[Callable[[str], str]] = None,
) -> Gtk.Menu:
    """Turn a description into a :class:`Gtk.Menu`.

    ``accel_lookup`` maps a config key (``"close_pane"``) to a display string
    (``"Ctrl+Shift+Q"``); when it returns nothing the shortcut hint is omitted.
    """
    menu = Gtk.Menu()
    for entry in items:
        if entry is SEPARATOR:
            separator = Gtk.SeparatorMenuItem()
            separator.show()
            menu.append(separator)
            continue
        label = entry.label
        if accel_lookup is not None and entry.accel_action:
            accel = accel_lookup(entry.accel_action)
            if accel:
                label = f"{label}\t{accel}"
        item = Gtk.MenuItem(label=label)
        item.set_sensitive(entry.enabled)
        if entry.hint:
            item.set_tooltip_text(entry.hint)
        item.connect("activate", lambda _w, action=entry.action: on_activate(action))
        item.show()
        menu.append(item)
    return menu
