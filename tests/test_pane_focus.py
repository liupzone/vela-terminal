"""Directional focus and the focus-ring state.

Regression 1: the focused pane looked identical to the others, because the ring
was a CSS ``box-shadow`` on a ``Gtk.Box`` (which paints no background) and the
``draw`` handler had the wrong signature, so it raised on every repaint.

Regression 2: ``focus_direction`` compared ``get_allocation()`` rectangles from
panes with different parents, so moving focus left/right picked the wrong pane
or none at all.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from vela.pane import Leaf, PaneContainer  # noqa: E402


class FakeView:
    """Minimal stand-in for TerminalView with the focus-ring API."""

    def __init__(self, name):
        self.name = name
        self.focused = False
        self.widget = Gtk.Box()

    def set_focused(self, focused):
        self.focused = bool(focused)

    def grab_focus(self):  # pragma: no cover - not exercised here
        pass

    def __repr__(self):
        return f"FakeView({self.name})"


class PaneContainerFocusTests(unittest.TestCase):
    def make_container(self, count=1):
        views = [FakeView(str(index)) for index in range(count)]
        container = PaneContainer(make_view=lambda: views.pop(0))
        first = container.bootstrap()
        return container, first

    def test_first_leaf_starts_focused(self):
        container, leaf = self.make_container()
        self.assertIs(container.active, leaf)
        self.assertTrue(leaf.view.focused)

    def test_set_active_moves_the_ring(self):
        container, first = self.make_container(2)
        second = container.split(first, Gtk.Orientation.HORIZONTAL)
        self.assertTrue(second.view.focused)
        self.assertFalse(first.view.focused)

        container.set_active(first)
        self.assertTrue(first.view.focused)
        self.assertFalse(second.view.focused)

    def test_set_focused_called_once_per_change(self):
        calls = []
        container, first = self.make_container(2)
        second = container.split(first, Gtk.Orientation.HORIZONTAL)
        first.view.set_focused = lambda value: calls.append(("first", value))
        second.view.set_focused = lambda value: calls.append(("second", value))
        container.set_active(first)
        self.assertEqual(calls, [("second", False), ("first", True)])

    def test_reactivating_the_same_leaf_keeps_it_focused(self):
        """Re-selecting the active pane must leave it (and only it) highlighted."""
        container, first = self.make_container(1)
        container.set_active(first)
        self.assertTrue(first.view.focused)

    def test_focus_direction_uses_window_coordinates(self):
        """Two side-by-side panes must resolve left/right correctly."""
        container, first = self.make_container(2)
        second = container.split(first, Gtk.Orientation.HORIZONTAL)
        # Lay the panes out so their allocations are meaningful.
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_default_size(800, 400)
        window.add(container)
        window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        container.set_active(second)
        self.assertTrue(container.focus_direction("left"))
        self.assertIs(container.active, first)

        self.assertTrue(container.focus_direction("right"))
        self.assertIs(container.active, second)

        window.destroy()

    def test_focus_direction_at_the_edge_returns_false(self):
        container, first = self.make_container(2)
        second = container.split(first, Gtk.Orientation.HORIZONTAL)
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_default_size(800, 400)
        window.add(container)
        window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        container.set_active(first)
        self.assertFalse(container.focus_direction("left"))
        window.destroy()

    def test_focus_direction_up_and_down(self):
        container, first = self.make_container(2)
        second = container.split(first, Gtk.Orientation.VERTICAL)
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_default_size(800, 400)
        window.add(container)
        window.show_all()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

        container.set_active(second)
        self.assertTrue(container.focus_direction("up"))
        self.assertIs(container.active, first)
        self.assertTrue(container.focus_direction("down"))
        self.assertIs(container.active, second)
        window.destroy()

    def test_focus_direction_ignored_while_zoomed(self):
        container, first = self.make_container(2)
        second = container.split(first, Gtk.Orientation.HORIZONTAL)
        container.zoomed = True
        self.assertFalse(container.focus_direction("left"))
        self.assertIs(container.active, second)

    def test_closing_the_active_pane_moves_the_ring(self):
        container, first = self.make_container(2)
        second = container.split(first, Gtk.Orientation.HORIZONTAL)
        self.assertTrue(second.view.focused)
        container.close(second)
        self.assertTrue(first.view.focused)


class FocusRingDrawingTests(unittest.TestCase):
    """The draw handler must accept the (widget, cr) signature GTK uses."""

    def test_draw_handler_signature(self):
        import inspect

        from vela.terminal import TerminalView

        parameters = list(
            inspect.signature(TerminalView._on_draw_border).parameters
        )
        # self, widget, cr — a third positional parameter would raise on every
        # repaint, which is exactly what happened before.
        self.assertEqual(parameters, ["self", "widget", "cr"])


if __name__ == "__main__":
    unittest.main()
