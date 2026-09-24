"""Side panels living inside the split-pane tree.

The pane tree is deliberately type-agnostic: a leaf holds a widget that
understands ``set_focused()``.  These tests cover the parts that do care about
the difference — which panes a terminal action targets, and how a tab is named.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from vela import panels  # noqa: E402
from vela import theme as theme_mod  # noqa: E402
from vela.pane import Leaf, PaneContainer, Split  # noqa: E402


class FakePanel(Gtk.Box):
    """A stand-in side panel with the lifecycle hooks PanelView forwards."""

    def __init__(self):
        super().__init__()
        self.started = 0
        self.stopped = 0
        self.themed = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def apply_theme(self, _theme):
        self.themed += 1


class PanelViewTests(unittest.TestCase):
    def setUp(self):
        self.theme = theme_mod.get(theme_mod.DEFAULT_THEME)
        self.panel = FakePanel()
        self.view = panels.PanelView(panels.KIND_SYSINFO, self.panel, self.theme)

    def test_reports_its_kind_and_title(self):
        self.assertEqual(self.view.kind, panels.KIND_SYSINFO)
        self.assertEqual(self.view.title, "系统性能")

    def test_start_and_stop_drive_the_panel(self):
        """The monitor samples only while its pane is alive."""
        self.view.start()
        self.assertEqual(self.panel.started, 1)
        self.view.stop()
        self.assertEqual(self.panel.stopped, 1)

    def test_terminate_stops_the_panel(self):
        self.view.terminate()
        self.assertEqual(self.panel.stopped, 1)

    def test_theme_is_forwarded(self):
        other = theme_mod.get("nord")
        self.view.apply_theme(other)
        self.assertEqual(self.panel.themed, 1)
        self.assertEqual(self.view.theme.name, "nord")

    def test_focus_flag_is_tracked(self):
        self.assertFalse(self.view.focused)
        self.view.set_focused(True)
        self.assertTrue(self.view.focused)
        self.view.set_focused(False)
        self.assertFalse(self.view.focused)

    def test_has_no_child_process(self):
        """Terminal-only attributes must exist so shared code can read them."""
        self.assertFalse(self.view.exited)
        self.assertEqual(self.view.child_pid, 0)

    def test_kind_labels_cover_every_kind(self):
        for kind in (
            panels.KIND_TERMINAL,
            panels.KIND_SYSINFO,
            panels.KIND_FILEBROWSER,
        ):
            self.assertTrue(panels.kind_label(kind))
            self.assertNotEqual(panels.kind_label(kind), kind)

    def test_is_terminal_only_for_terminal(self):
        self.assertTrue(panels.is_terminal(panels.KIND_TERMINAL))
        self.assertFalse(panels.is_terminal(panels.KIND_SYSINFO))
        self.assertFalse(panels.is_terminal(panels.KIND_FILEBROWSER))


class MixedTreeTests(unittest.TestCase):
    """Splitting, closing and focus with terminals and panels side by side."""

    def setUp(self):
        self.theme = theme_mod.get(theme_mod.DEFAULT_THEME)
        self.created = []

    def make_view(self, kind):
        view = panels.PanelView(kind, FakePanel(), self.theme)
        self.created.append(view)
        return view

    def make_container(self):
        container = PaneContainer(make_view=self.make_view)
        leaf = container.bootstrap(panels.KIND_TERMINAL)
        return container, leaf

    def test_bootstrap_records_the_kind(self):
        container, leaf = self.make_container()
        self.assertEqual(leaf.kind, panels.KIND_TERMINAL)

    def test_split_creates_the_requested_kind(self):
        container, leaf = self.make_container()
        new_leaf = container.split(
            leaf, Gtk.Orientation.HORIZONTAL, kind=panels.KIND_SYSINFO
        )
        self.assertEqual(new_leaf.kind, panels.KIND_SYSINFO)
        self.assertEqual(
            [item.kind for item in container.leaves()],
            [panels.KIND_TERMINAL, panels.KIND_SYSINFO],
        )

    def test_a_panel_pane_can_be_split_again(self):
        container, leaf = self.make_container()
        monitor = container.split(leaf, Gtk.Orientation.HORIZONTAL,
                                  kind=panels.KIND_SYSINFO)
        files = container.split(monitor, Gtk.Orientation.VERTICAL,
                                kind=panels.KIND_FILEBROWSER)
        self.assertEqual(files.kind, panels.KIND_FILEBROWSER)
        self.assertEqual(
            [item.kind for item in container.leaves()],
            [
                panels.KIND_TERMINAL,
                panels.KIND_SYSINFO,
                panels.KIND_FILEBROWSER,
            ],
        )

    def test_panel_panes_take_part_in_focus(self):
        container, leaf = self.make_container()
        monitor = container.split(leaf, Gtk.Orientation.HORIZONTAL,
                                  kind=panels.KIND_SYSINFO)
        self.assertTrue(monitor.view.focused)
        self.assertFalse(leaf.view.focused)
        container.set_active(leaf)
        self.assertTrue(leaf.view.focused)
        self.assertFalse(monitor.view.focused)

    def test_closing_a_panel_pane_keeps_the_terminal(self):
        container, leaf = self.make_container()
        monitor = container.split(leaf, Gtk.Orientation.HORIZONTAL,
                                  kind=panels.KIND_SYSINFO)
        container.close(monitor)
        self.assertEqual([item.kind for item in container.leaves()],
                         [panels.KIND_TERMINAL])
        self.assertIs(container.active, leaf)
        self.assertTrue(leaf.view.focused)

    def test_closing_the_terminal_keeps_the_panel(self):
        container, leaf = self.make_container()
        monitor = container.split(leaf, Gtk.Orientation.HORIZONTAL,
                                  kind=panels.KIND_SYSINFO)
        container.close(leaf)
        self.assertEqual([item.kind for item in container.leaves()],
                         [panels.KIND_SYSINFO])

    def test_zoom_works_with_a_panel_active(self):
        container, leaf = self.make_container()
        monitor = container.split(leaf, Gtk.Orientation.HORIZONTAL,
                                  kind=panels.KIND_SYSINFO)
        container.set_active(monitor)
        self.assertTrue(container.toggle_zoom())
        self.assertTrue(container.zoomed)
        container.unzoom()
        self.assertFalse(container.zoomed)

    def test_many_panels_do_not_confuse_the_tree(self):
        container, leaf = self.make_container()
        kinds = [
            panels.KIND_SYSINFO,
            panels.KIND_FILEBROWSER,
            panels.KIND_SYSINFO,
            panels.KIND_TERMINAL,
        ]
        target = leaf
        for kind in kinds:
            target = container.split(target, Gtk.Orientation.HORIZONTAL, kind=kind)
        self.assertEqual(len(container.leaves()), 5)
        self.assertEqual(
            sorted(item.kind for item in container.leaves()),
            sorted([panels.KIND_TERMINAL] + kinds),
        )

    def test_unknown_kind_is_rejected_by_the_factory(self):
        def factory(kind):
            if kind not in (
                panels.KIND_TERMINAL,
                panels.KIND_SYSINFO,
                panels.KIND_FILEBROWSER,
            ):
                raise ValueError(f"未知分屏类型：{kind}")
            return panels.PanelView(kind, FakePanel(), self.theme)

        container = PaneContainer(make_view=factory)
        container.bootstrap(panels.KIND_TERMINAL)
        with self.assertRaises(ValueError):
            container.split(
                container.active, Gtk.Orientation.HORIZONTAL, kind="nonsense"
            )

    def test_leaf_defaults_to_terminal_kind(self):
        """Existing callers that do not pass a kind keep working."""
        leaf = Leaf(Gtk.Box())
        self.assertEqual(leaf.kind, panels.KIND_TERMINAL)


class ReplacedTerminalTests(unittest.TestCase):
    """Switching a pane's kind must not look like a crash.

    Regression: replacing the terminal called ``terminate()``, whose SIGHUP made
    the shell exit, and the exit handler reported "进程异常退出（被信号 1 终止）"
    for something the user asked for.
    """

    def test_detached_terminal_stays_quiet(self):
        """A view marked detached must not emit child-exited."""
        import vela.terminal as terminal_mod

        class FakeTerminal:
            def __init__(self):
                self.emitted = []

            def emit(self, name, *args):
                self.emitted.append(name)

        view = terminal_mod.TerminalView.__new__(terminal_mod.TerminalView)
        view.exited = False
        view.detached = True
        view.exit_status = None
        view.config = _ConfigStub()
        view._terminal_stub = FakeTerminal()
        view.emit = view._terminal_stub.emit
        view.show_message = lambda _text: None

        terminal_mod.TerminalView._on_child_exited(view, None, 1)
        self.assertTrue(view.exited)
        self.assertEqual(view._terminal_stub.emitted, [])

    def test_terminate_marks_the_view_detached(self):
        """Killing a pane on purpose must not be reported as a failure."""
        import vela.terminal as terminal_mod

        view = terminal_mod.TerminalView.__new__(terminal_mod.TerminalView)
        view.exited = False
        view.detached = False
        view._child_pid = 0  # no real process to signal
        terminal_mod.TerminalView.terminate(view)
        self.assertTrue(view.detached)

    def test_normal_exit_still_reports(self):
        """A shell the user exited themselves must still be reported."""
        import vela.terminal as terminal_mod

        class FakeTerminal:
            def __init__(self):
                self.emitted = []

            def emit(self, name, *args):
                self.emitted.append(name)

        view = terminal_mod.TerminalView.__new__(terminal_mod.TerminalView)
        view.exited = False
        view.detached = False
        view.exit_status = None
        view.config = _ConfigStub()
        view._terminal_stub = FakeTerminal()
        view.emit = view._terminal_stub.emit
        view.show_message = lambda _text: None

        terminal_mod.TerminalView._on_child_exited(view, None, 0)
        self.assertIn("child-exited", view._terminal_stub.emitted)


class _ConfigStub:
    """Minimal config for the terminal exit path."""

    def get(self, key, fallback=None):
        if key == "behavior.show_exit_hint":
            return False
        return fallback


class PaneKindHelpersTests(unittest.TestCase):
    """The helpers a tab uses to tell terminals from panels."""

    def make_tab_like(self, kinds):
        """A tiny object exposing the same helpers as Tab, over a real tree."""
        theme = theme_mod.get(theme_mod.DEFAULT_THEME)
        container = PaneContainer(
            make_view=lambda kind: panels.PanelView(kind, FakePanel(), theme)
        )
        leaf = container.bootstrap(kinds[0])
        target = leaf
        for kind in kinds[1:]:
            target = container.split(target, Gtk.Orientation.HORIZONTAL, kind=kind)

        class TabLike:
            def __init__(self, container):
                self.container = container

            def terminals(self):
                return [
                    item.view
                    for item in self.container.leaves()
                    if panels.is_terminal(item.kind)
                ]

            def panel_panes(self):
                return [
                    item for item in self.container.leaves()
                    if not panels.is_terminal(item.kind)
                ]

        return TabLike(container)

    def test_terminals_filters_panels(self):
        tab = self.make_tab_like(
            [
                panels.KIND_TERMINAL,
                panels.KIND_SYSINFO,
                panels.KIND_FILEBROWSER,
                panels.KIND_TERMINAL,
            ]
        )
        self.assertEqual(len(tab.terminals()), 2)
        self.assertEqual(len(tab.panel_panes()), 2)

    def test_panels_only_tab_has_no_terminals(self):
        tab = self.make_tab_like(
            [panels.KIND_SYSINFO, panels.KIND_FILEBROWSER]
        )
        self.assertEqual(tab.terminals(), [])
        self.assertEqual(len(tab.panel_panes()), 2)

    def test_all_terminals_tab_has_no_panels(self):
        tab = self.make_tab_like(
            [panels.KIND_TERMINAL, panels.KIND_TERMINAL]
        )
        self.assertEqual(len(tab.terminals()), 2)
        self.assertEqual(tab.panel_panes(), [])


if __name__ == "__main__":
    unittest.main()
