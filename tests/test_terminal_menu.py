"""Terminal context menu: contents, grouping and state-dependent entries."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import config as config_mod  # noqa: E402
from vela import terminalmenu as tm  # noqa: E402


def labels(items):
    return [entry.label for entry in items if entry is not tm.SEPARATOR]


def actions(items):
    return [entry.action for entry in items if entry is not tm.SEPARATOR]


def groups(items):
    """Items split into groups by the separators."""
    result = [[]]
    for entry in items:
        if entry is tm.SEPARATOR:
            result.append([])
        else:
            result[-1].append(entry)
    return result


class MenuContentsTests(unittest.TestCase):
    def setUp(self):
        self.items = tm.menu_items(has_selection=True, pane_count=2)

    def test_expected_actions_present(self):
        present = actions(self.items)
        for action in (
            "copy",
            "paste",
            "paste-escaped",
            "split-vertical",
            "split-horizontal",
            "close-pane",
            "zoom-pane",
            "select-all",
            "clear",
            "reset",
            "reveal-path",
        ):
            self.assertIn(action, present, action)

    def test_grouping(self):
        grouped = groups(self.items)
        self.assertEqual(len(grouped), 4, "menu should have four groups")
        self.assertEqual(
            [entry.action for entry in grouped[0]],
            ["copy", "paste", "paste-escaped"],
        )
        self.assertEqual(
            [entry.action for entry in grouped[1]],
            ["split-vertical", "split-horizontal", "close-pane", "zoom-pane"],
        )
        self.assertEqual(
            [entry.action for entry in grouped[2]],
            ["select-all", "clear", "reset"],
        )
        self.assertIn("reveal-path", [entry.action for entry in grouped[3]])

    def test_no_duplicate_actions(self):
        present = actions(self.items)
        self.assertEqual(len(present), len(set(present)))

    def test_every_item_has_a_shortcut_key(self):
        for entry in self.items:
            if entry is tm.SEPARATOR:
                continue
            with self.subTest(action=entry.action):
                self.assertTrue(entry.accel_action, entry.action)

    def test_labels_are_non_empty(self):
        for entry in self.items:
            if entry is tm.SEPARATOR:
                continue
            with self.subTest(action=entry.action):
                self.assertTrue(entry.label.strip())


class MenuStateTests(unittest.TestCase):
    def test_copy_disabled_without_selection(self):
        items = tm.menu_items(has_selection=False, pane_count=2)
        by_action = {e.action: e for e in items if e is not tm.SEPARATOR}
        self.assertFalse(by_action["copy"].enabled)
        self.assertFalse(by_action["open-selection"].enabled)
        self.assertTrue(by_action["paste"].enabled)

    def test_copy_enabled_with_selection(self):
        items = tm.menu_items(has_selection=True, pane_count=2)
        by_action = {e.action: e for e in items if e is not tm.SEPARATOR}
        self.assertTrue(by_action["copy"].enabled)
        self.assertTrue(by_action["open-selection"].enabled)

    def test_close_pane_label_changes_for_single_pane(self):
        single = {e.action: e for e in tm.menu_items(pane_count=1)
                  if e is not tm.SEPARATOR}
        multi = {e.action: e for e in tm.menu_items(pane_count=2)
                 if e is not tm.SEPARATOR}
        self.assertEqual(single["close-pane"].label, "关闭标签页")
        self.assertEqual(multi["close-pane"].label, "关闭当前分屏")

    def test_zoom_disabled_with_single_pane(self):
        single = {e.action: e for e in tm.menu_items(pane_count=1)
                  if e is not tm.SEPARATOR}
        self.assertFalse(single["zoom-pane"].enabled)

    def test_zoom_label_reflects_state(self):
        normal = {e.action: e for e in tm.menu_items(pane_count=2, zoomed=False)
                  if e is not tm.SEPARATOR}
        zoomed = {e.action: e for e in tm.menu_items(pane_count=2, zoomed=True)
                  if e is not tm.SEPARATOR}
        self.assertEqual(normal["zoom-pane"].label, "最大化当前分屏")
        self.assertEqual(zoomed["zoom-pane"].label, "还原分屏布局")
        self.assertTrue(zoomed["zoom-pane"].enabled)

    def test_zoom_enabled_when_zoomed_even_with_one_pane(self):
        items = {e.action: e for e in tm.menu_items(pane_count=1, zoomed=True)
                 if e is not tm.SEPARATOR}
        self.assertTrue(items["zoom-pane"].enabled)


class BuildMenuTests(unittest.TestCase):
    """The description must turn into a working Gtk.Menu."""

    def test_menu_is_built_with_the_right_shape(self):
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        items = tm.menu_items(has_selection=True, pane_count=2)
        menu = tm.build_menu(items, lambda _action: None)
        children = menu.get_children()
        expected = len([i for i in items if i is not tm.SEPARATOR])
        separators = len([i for i in items if i is tm.SEPARATOR])
        self.assertEqual(len(children), expected + separators)
        self.assertEqual(
            sum(1 for c in children if isinstance(c, Gtk.SeparatorMenuItem)),
            separators,
        )

    def test_activate_calls_back_with_the_action_name(self):
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        fired = []
        menu = tm.build_menu(tm.menu_items(has_selection=True), fired.append)
        target = next(
            c for c in menu.get_children()
            if isinstance(c, Gtk.MenuItem) and not isinstance(c, Gtk.SeparatorMenuItem)
        )
        target.activate()
        self.assertEqual(fired, ["copy"])

    def test_accelerator_hint_is_appended(self):
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        bindings = config_mod.Config().keybindings()
        from vela import keymap

        menu = tm.build_menu(
            tm.menu_items(has_selection=True),
            lambda _action: None,
            lambda key: keymap.display(bindings.get(key, "")),
        )
        texts = [
            c.get_label() for c in menu.get_children()
            if isinstance(c, Gtk.MenuItem) and not isinstance(c, Gtk.SeparatorMenuItem)
        ]
        self.assertTrue(any("Ctrl+Shift+C" in text for text in texts), texts)

    def test_disabled_item_is_insensitive(self):
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        menu = tm.build_menu(
            tm.menu_items(has_selection=False), lambda _action: None
        )
        first = menu.get_children()[0]
        self.assertFalse(first.get_sensitive())

    def test_unknown_accel_key_omits_the_hint(self):
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        menu = tm.build_menu(
            tm.menu_items(has_selection=True), lambda _action: None, lambda _k: ""
        )
        texts = [
            c.get_label() for c in menu.get_children()
            if isinstance(c, Gtk.MenuItem) and not isinstance(c, Gtk.SeparatorMenuItem)
        ]
        self.assertFalse(any("\t" in text for text in texts), texts)


class ActionCoverageTests(unittest.TestCase):
    """Every menu action must be one the window actually implements."""

    def test_actions_exist_in_the_window(self):
        import re

        window_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "vela",
            "window.py",
        )
        with open(window_path, encoding="utf-8") as handle:
            source = handle.read()
        registered = set(re.findall(r'\("([a-z0-9-]+)", lambda', source))
        for entry in tm.menu_items(has_selection=True, pane_count=2):
            if entry is tm.SEPARATOR:
                continue
            with self.subTest(action=entry.action):
                self.assertIn(
                    entry.action,
                    registered,
                    f"菜单项 {entry.label!r} 指向未注册的动作 {entry.action!r}",
                )

    def test_accel_keys_exist_in_config(self):
        bindings = config_mod.Config().keybindings()
        for entry in tm.menu_items(has_selection=True, pane_count=2):
            if entry is tm.SEPARATOR:
                continue
            with self.subTest(action=entry.action):
                self.assertIn(
                    entry.accel_action,
                    bindings,
                    f"{entry.action} 的快捷键配置键 {entry.accel_action!r} 不存在",
                )


if __name__ == "__main__":
    unittest.main()
