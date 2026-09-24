"""Saving and restoring pane layouts.

The interesting parts are pure data: round-tripping the tree, tolerating damaged
or outdated files, and handling directories that no longer exist.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import config as config_mod  # noqa: E402
from vela import layout  # noqa: E402


def nested_spec():
    """terminal | (sysinfo / filebrowser) — the shape used in the docs."""
    return layout.LayoutSpec(
        name="demo",
        tabs=[
            layout.TabSpec(
                title="dev",
                tree=layout.SplitSpec(
                    orientation="h",
                    position=540,
                    first=layout.PaneSpec(kind="terminal", cwd="/tmp"),
                    second=layout.SplitSpec(
                        orientation="v",
                        position=200,
                        first=layout.PaneSpec(kind="sysinfo"),
                        second=layout.PaneSpec(kind="filebrowser"),
                    ),
                ),
            )
        ],
    )


class SerialisationTests(unittest.TestCase):
    def test_round_trip_preserves_the_tree(self):
        spec = nested_spec()
        restored = layout.parse_layout(spec.to_dict())
        self.assertIsNotNone(restored)
        self.assertEqual(restored.pane_count, 3)
        self.assertEqual(
            [pane.kind for pane in layout.iter_panes(restored.tabs[0].tree)],
            ["terminal", "sysinfo", "filebrowser"],
        )

    def test_round_trip_preserves_divider_positions(self):
        restored = layout.parse_layout(nested_spec().to_dict())
        tree = restored.tabs[0].tree
        self.assertEqual(tree.position, 540)
        self.assertEqual(tree.second.position, 200)
        self.assertEqual(tree.orientation, "h")
        self.assertEqual(tree.second.orientation, "v")

    def test_round_trip_preserves_tab_name_and_directory(self):
        restored = layout.parse_layout(nested_spec().to_dict())
        self.assertEqual(restored.tabs[0].title, "dev")
        first = next(layout.iter_panes(restored.tabs[0].tree))
        self.assertEqual(first.cwd, "/tmp")

    def test_single_pane(self):
        spec = layout.LayoutSpec(tabs=[layout.TabSpec(tree=layout.PaneSpec())])
        restored = layout.parse_layout(spec.to_dict())
        self.assertEqual(restored.pane_count, 1)

    def test_version_is_recorded(self):
        self.assertEqual(nested_spec().to_dict()["version"], layout.LAYOUT_VERSION)

    def test_pane_count_of_a_deep_tree(self):
        node = layout.PaneSpec()
        for _ in range(5):
            node = layout.SplitSpec(first=node, second=layout.PaneSpec())
        self.assertEqual(layout.count_panes(node), 6)


class DamagedInputTests(unittest.TestCase):
    """Bad data must degrade, never crash the restore."""

    def test_unknown_pane_kind_becomes_a_terminal(self):
        spec = layout.from_dict({"type": "pane", "kind": "hologram"})
        self.assertEqual(spec.kind, "terminal")

    def test_unknown_orientation_defaults_to_horizontal(self):
        spec = layout.from_dict({"type": "split", "orientation": "diagonal"})
        self.assertEqual(spec.orientation, "h")

    def test_missing_children_become_empty_panes(self):
        spec = layout.from_dict({"type": "split"})
        self.assertEqual(layout.count_panes(spec), 2)
        self.assertIsInstance(spec.first, layout.PaneSpec)

    def test_non_dict_node_becomes_a_pane(self):
        self.assertIsInstance(layout.from_dict("nonsense"), layout.PaneSpec)
        self.assertIsInstance(layout.from_dict(None), layout.PaneSpec)

    def test_excessive_nesting_is_rejected(self):
        node = {"type": "pane"}
        for _ in range(60):
            node = {"type": "split", "first": node, "second": {"type": "pane"}}
        with self.assertRaises(layout.LayoutError):
            layout.from_dict(node)

    def test_bad_position_falls_back_to_zero(self):
        spec = layout.from_dict({"type": "split", "position": "wide"})
        self.assertEqual(spec.position, 0)

    def test_parse_layout_rejects_empty(self):
        self.assertIsNone(layout.parse_layout({}))
        self.assertIsNone(layout.parse_layout({"tabs": []}))
        self.assertIsNone(layout.parse_layout("not a layout"))
        self.assertIsNone(layout.parse_layout(None))

    def test_parse_layout_skips_unusable_tabs(self):
        payload = {
            "tabs": ["junk", {"tree": {"type": "pane"}}],
        }
        spec = layout.parse_layout(payload)
        self.assertIsNotNone(spec)
        self.assertEqual(len(spec.tabs), 1)

    def test_normalize_fills_in_a_missing_tree(self):
        spec = layout.normalize(layout.LayoutSpec(tabs=[layout.TabSpec()]))
        self.assertIsInstance(spec.tabs[0].tree, layout.PaneSpec)

    def test_normalize_adds_a_tab_when_empty(self):
        spec = layout.normalize(layout.LayoutSpec(tabs=[]))
        self.assertEqual(len(spec.tabs), 1)
        self.assertIsInstance(spec.tabs[0].tree, layout.PaneSpec)


class MissingDirectoryTests(unittest.TestCase):
    def test_missing_directories_are_cleared_and_reported(self):
        spec = layout.LayoutSpec(
            tabs=[
                layout.TabSpec(
                    tree=layout.SplitSpec(
                        first=layout.PaneSpec(cwd="/definitely/not/here"),
                        second=layout.PaneSpec(cwd="/tmp"),
                    )
                )
            ]
        )
        changed = layout.prune_missing_directories(spec)
        self.assertEqual(changed, ["/definitely/not/here"])
        panes = list(layout.iter_panes(spec.tabs[0].tree))
        self.assertEqual(panes[0].cwd, "")
        self.assertEqual(panes[1].cwd, "/tmp")

    def test_existing_directories_are_kept(self):
        spec = layout.LayoutSpec(
            tabs=[layout.TabSpec(tree=layout.PaneSpec(cwd=os.path.expanduser("~")))]
        )
        self.assertEqual(layout.prune_missing_directories(spec), [])


class NamedLayoutTests(unittest.TestCase):
    def setUp(self):
        self.config = config_mod.Config()

    def test_store_and_read_back(self):
        layout.store_named_layout(self.config, nested_spec(), "工作")
        saved = layout.named_layouts(self.config)
        self.assertIn("工作", saved)
        self.assertEqual(saved["工作"].pane_count, 3)

    def test_storing_again_overwrites(self):
        layout.store_named_layout(self.config, nested_spec(), "工作")
        simpler = layout.LayoutSpec(tabs=[layout.TabSpec(tree=layout.PaneSpec())])
        layout.store_named_layout(self.config, simpler, "工作")
        self.assertEqual(layout.named_layouts(self.config)["工作"].pane_count, 1)

    def test_name_falls_back_to_the_spec(self):
        spec = nested_spec()  # its name is "demo"
        key = layout.store_named_layout(self.config, spec)
        self.assertEqual(key, "demo")

    def test_empty_name_is_rejected(self):
        with self.assertRaises(layout.LayoutError):
            layout.store_named_layout(self.config, nested_spec(), "   ")

    def test_remove(self):
        layout.store_named_layout(self.config, nested_spec(), "工作")
        self.assertTrue(layout.remove_named_layout(self.config, "工作"))
        self.assertNotIn("工作", layout.named_layouts(self.config))
        self.assertFalse(layout.remove_named_layout(self.config, "工作"))

    def test_unknown_entries_are_skipped(self):
        self.config.section("layouts")["broken"] = {"nonsense": True}
        self.assertEqual(layout.named_layouts(self.config), {})

    def test_survives_a_config_round_trip(self):
        """A saved layout must come back after writing and reading the config."""
        layout.store_named_layout(self.config, nested_spec(), "工作")
        text = self.config.to_toml()
        reloaded = config_mod.Config(config_mod.minitoml.loads(text))
        saved = layout.named_layouts(reloaded)
        self.assertIn("工作", saved)
        self.assertEqual(saved["工作"].pane_count, 3)


class SessionFileTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._temp.name, "session.json")

    def tearDown(self):
        self._temp.cleanup()

    def test_save_and_load(self):
        layout.save_session(nested_spec(), self.path)
        loaded = layout.load_session(self.path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.pane_count, 3)

    def test_missing_file_is_not_an_error(self):
        self.assertIsNone(layout.load_session(self.path))

    def test_corrupt_file_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertIsNone(layout.load_session(self.path))

    def test_file_without_tabs_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1}, handle)
        self.assertIsNone(layout.load_session(self.path))

    def test_save_is_atomic(self):
        layout.save_session(nested_spec(), self.path)
        leftovers = [
            name for name in os.listdir(self._temp.name) if "tmp" in name
        ]
        self.assertEqual(leftovers, [])

    def test_clear(self):
        layout.save_session(nested_spec(), self.path)
        self.assertTrue(layout.clear_session(self.path))
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(layout.clear_session(self.path))

    def test_save_creates_the_directory(self):
        nested = os.path.join(self._temp.name, "deep", "session.json")
        layout.save_session(nested_spec(), nested)
        self.assertTrue(os.path.exists(nested))


if __name__ == "__main__":
    unittest.main()
