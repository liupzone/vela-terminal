"""Rebuilding a pane tree from a saved layout must bring the panes to life.

Regression: ``build_from_spec`` created each view but never spawned the shell,
while ``split()`` did.  A restored layout therefore came back as a grid of empty
frames with no prompt in any of them, and the saved working directory was
dropped on the floor as well.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from vela import layout as layout_mod  # noqa: E402
from vela.pane import PaneContainer  # noqa: E402


class FakeTerminal:
    """Records how a terminal view was started."""

    def __init__(self) -> None:
        self.spawns = []
        self.starts = 0

    def spawn(self, argv=None, cwd=None) -> None:
        self.spawns.append(cwd)

    def terminate(self) -> None:
        pass


class FakePanel:
    def __init__(self) -> None:
        self.starts = 0

    def start(self) -> None:
        self.starts += 1

    def terminate(self) -> None:
        pass


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.made = {}

        def make_view(kind):
            view = FakeTerminal() if kind == "terminal" else FakePanel()
            self.made.setdefault(kind, []).append(view)
            return view

        self.container = PaneContainer(make_view=make_view)
        self.make_view = make_view

    def build(self, spec):
        return self.container.build_from_spec(spec, self.make_view)

    def test_a_single_terminal_is_spawned(self):
        self.build(layout_mod.PaneSpec(kind="terminal", cwd="/tmp"))
        view = self.made["terminal"][0]
        self.assertEqual(view.spawns, ["/tmp"])

    def test_a_nested_tree_spawns_every_terminal(self):
        spec = layout_mod.SplitSpec(
            orientation="h",
            position=300,
            first=layout_mod.PaneSpec(kind="terminal", cwd="/etc"),
            second=layout_mod.SplitSpec(
                orientation="v",
                position=200,
                first=layout_mod.PaneSpec(kind="terminal", cwd="/usr"),
                second=layout_mod.PaneSpec(kind="terminal", cwd="/var"),
            ),
        )
        self.build(spec)
        self.assertEqual(
            [view.spawns for view in self.made["terminal"]],
            [["/etc"], ["/usr"], ["/var"]],
        )

    def test_the_tree_shape_is_preserved(self):
        spec = layout_mod.SplitSpec(
            orientation="h",
            position=300,
            first=layout_mod.PaneSpec(kind="terminal"),
            second=layout_mod.PaneSpec(kind="sysinfo"),
        )
        self.build(spec)
        kinds = [leaf.kind for leaf in self.container.leaves()]
        self.assertEqual(kinds, ["terminal", "sysinfo"])

    def test_a_missing_cwd_still_spawns(self):
        """An empty cwd means "the default directory", not "do not start"."""
        self.build(layout_mod.PaneSpec(kind="terminal", cwd=""))
        self.assertEqual(self.made["terminal"][0].spawns, [None])

    def test_a_panel_is_started_instead_of_spawned(self):
        self.build(layout_mod.PaneSpec(kind="sysinfo"))
        self.assertEqual(self.made["sysinfo"][0].starts, 1)

    def test_panels_are_not_spawned(self):
        self.build(layout_mod.PaneSpec(kind="filebrowser"))
        panel = self.made["filebrowser"][0]
        self.assertFalse(hasattr(panel, "spawns"))


class RoundTripTests(unittest.TestCase):
    """The saved directory has to survive the round trip to disk and back."""

    def test_cwd_survives_serialisation(self):
        spec = layout_mod.LayoutSpec(
            name="t",
            tabs=[
                layout_mod.TabSpec(
                    title="dev",
                    tree=layout_mod.PaneSpec(kind="terminal", cwd="/tmp"),
                )
            ],
        )
        restored = layout_mod.parse_layout(spec.to_dict(), "t")
        self.assertIsNotNone(restored)
        panes = list(layout_mod.iter_panes(restored.tabs[0].tree))
        self.assertEqual(panes[0].cwd, "/tmp")


if __name__ == "__main__":
    unittest.main()
