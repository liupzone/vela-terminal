"""Split-pane tree logic that does not need a display."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from vela.pane import Leaf, Split  # noqa: E402


class TreeTests(unittest.TestCase):
    def test_single_leaf(self):
        leaf = Leaf(object())
        self.assertEqual(list(leaf.leaves()), [leaf])
        self.assertIsNone(leaf.parent)

    def test_split_tracks_parents(self):
        first = Leaf("a")
        second = Leaf("b")
        split = Split(Gtk.Orientation.HORIZONTAL, first, second)
        self.assertIs(first.parent, split)
        self.assertIs(second.parent, split)
        self.assertEqual(list(split.leaves()), [first, second])

    def test_nested_split_order_is_depth_first(self):
        a, b, c = Leaf("a"), Leaf("b"), Leaf("c")
        inner = Split(Gtk.Orientation.VERTICAL, b, c)
        outer = Split(Gtk.Orientation.HORIZONTAL, a, inner)
        self.assertEqual(list(outer.leaves()), [a, b, c])
        self.assertIs(inner.parent, outer)
        self.assertIs(b.parent, inner)

    def test_orientation_is_remembered(self):
        split = Split(Gtk.Orientation.VERTICAL, Leaf("a"), Leaf("b"))
        self.assertEqual(split.orientation, Gtk.Orientation.VERTICAL)

    def test_repr_is_informative(self):
        split = Split(Gtk.Orientation.HORIZONTAL, Leaf("a"), Leaf("b"))
        self.assertIn("Split(h", repr(split))


if __name__ == "__main__":
    unittest.main()
