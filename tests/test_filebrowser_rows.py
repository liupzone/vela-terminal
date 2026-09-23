"""Row activation handling for the file panel.

Regression: the ``row-activated`` signal passes a ``Gtk.TreePath`` while
PyGObject's ``Gtk.TreeView.row_activated()`` override takes a ``Gtk.TreeIter``.
The handler assumed a TreeIter, so a real double-click raised inside the handler
(silently swallowed by GTK) and nothing happened.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from vela.filebrowser import (  # noqa: E402
    COL_IS_DIR,
    COL_PATH,
    FileBrowserPanel,
)


class AsIterTests(unittest.TestCase):
    """``_as_iter`` accepts everything the signal or a caller may hand over."""

    def setUp(self):
        self.store = Gtk.ListStore(str, str, str, str, object, str, bool, str)
        self.store.append(["..", "", "", "", None, "/parent", True, ""])
        self.store.append(["sub", "drwxr-xr-x", "now", "", None, "/tmp/sub", True, ""])
        self.store.append(["a.txt", "-rw-r--r--", "now", "1B", None, "/tmp/a.txt", False, ""])
        # Bind the real method to a lightweight stand-in holding just the store.
        panel = FileBrowserPanel.__new__(FileBrowserPanel)
        panel.store = self.store
        self.panel = panel

    def test_accepts_tree_path(self):
        """This is the type the row-activated signal actually delivers."""
        tree_iter = self.panel._as_iter(Gtk.TreePath.new_from_indices([1]))
        self.assertIsNotNone(tree_iter)
        self.assertEqual(self.store.get_value(tree_iter, COL_PATH), "/tmp/sub")

    def test_accepts_tree_iter(self):
        tree_iter = self.store.get_iter(Gtk.TreePath.new_from_indices([2]))
        self.assertIs(self.panel._as_iter(tree_iter), tree_iter)

    def test_accepts_model_path_string(self):
        tree_iter = self.panel._as_iter("0")
        self.assertIsNotNone(tree_iter)
        self.assertEqual(self.store.get_value(tree_iter, COL_PATH), "/parent")

    def test_none_stays_none(self):
        self.assertIsNone(self.panel._as_iter(None))

    def test_unknown_type_is_rejected(self):
        self.assertIsNone(self.panel._as_iter(42))

    def test_out_of_range_path_is_rejected(self):
        self.assertIsNone(self.panel._as_iter(Gtk.TreePath.new_from_indices([99])))


class RowActivatedRoutingTests(unittest.TestCase):
    """The handler must route directories and files to the right action."""

    def make_panel(self):
        store = Gtk.ListStore(str, str, str, str, object, str, bool, str)
        store.append(["sub", "drwxr-xr-x", "now", "", None, "/tmp/sub", True, ""])
        store.append(["a.txt", "-rw-r--r--", "now", "1B", None, "/tmp/a.txt", False, ""])
        panel = FileBrowserPanel.__new__(FileBrowserPanel)
        panel.store = store
        panel.navigated = []
        panel.opened = []
        panel.navigate = lambda path: panel.navigated.append(path) or True
        panel.open_path = lambda path: panel.opened.append(path) or True
        return panel, store

    def test_directory_row_navigates(self):
        panel, _store = self.make_panel()
        panel._on_row_activated(None, Gtk.TreePath.new_from_indices([0]), None)
        self.assertEqual(panel.navigated, ["/tmp/sub"])
        self.assertEqual(panel.opened, [])

    def test_file_row_opens(self):
        panel, _store = self.make_panel()
        panel._on_row_activated(None, Gtk.TreePath.new_from_indices([1]), None)
        self.assertEqual(panel.opened, ["/tmp/a.txt"])
        self.assertEqual(panel.navigated, [])

    def test_tree_iter_also_works(self):
        panel, store = self.make_panel()
        tree_iter = store.get_iter(Gtk.TreePath.new_from_indices([0]))
        panel._on_row_activated(None, tree_iter, None)
        self.assertEqual(panel.navigated, ["/tmp/sub"])

    def test_empty_path_is_ignored(self):
        panel, store = self.make_panel()
        store.append(["empty", "", "", "", None, "", False, ""])
        panel._on_row_activated(None, Gtk.TreePath.new_from_indices([2]), None)
        self.assertEqual((panel.navigated, panel.opened), ([], []))

    def test_bad_row_does_not_raise(self):
        panel, _store = self.make_panel()
        panel._on_row_activated(None, None, None)
        panel._on_row_activated(None, 42, None)
        self.assertEqual((panel.navigated, panel.opened), ([], []))


if __name__ == "__main__":
    unittest.main()
