"""The shipped default shortcuts must be usable out of the box."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import config as config_mod  # noqa: E402
from vela import keymap  # noqa: E402


class DefaultKeybindingTests(unittest.TestCase):
    def setUp(self):
        self.bindings = config_mod.Config().keybindings()

    def test_no_default_shortcut_is_bound_twice(self):
        """A duplicate would make one action unreachable and show a warning."""
        conflicts = keymap.find_conflicts(self.bindings)
        self.assertEqual(
            conflicts,
            [],
            "默认快捷键冲突：" + "; ".join(
                f"{keymap.display(accel)} 同时绑定 {first} 与 {second}"
                for accel, first, second in conflicts
            ),
        )

    def test_every_default_shortcut_parses(self):
        for action, accelerator in self.bindings.items():
            with self.subTest(action=action):
                self.assertTrue(
                    keymap.normalise(accelerator),
                    f"{action} 的快捷键无法解析：{accelerator!r}",
                )

    def test_shortcuts_are_not_empty(self):
        for action, accelerator in self.bindings.items():
            with self.subTest(action=action):
                self.assertTrue(accelerator.strip(), f"{action} 没有快捷键")

    def test_expected_actions_are_bound(self):
        for action in (
            "new_tab",
            "close_tab",
            "split_vertical",
            "split_horizontal",
            "close_pane",
            "find",
            "command_palette",
            "toggle_sysinfo",
            "toggle_filebrowser",
            "open_path",
            "quit",
        ):
            self.assertIn(action, self.bindings, action)

    def test_no_shortcut_shadows_a_terminal_essential(self):
        """Ctrl+C / Ctrl+D / Ctrl+Z must stay with the shell."""
        reserved = {"<Ctrl>C", "<Ctrl>D", "<Ctrl>Z", "<Ctrl>L", "<Ctrl>A", "<Ctrl>E"}
        used = {keymap.normalise(accel) for accel in self.bindings.values()}
        self.assertEqual(reserved & used, set())


if __name__ == "__main__":
    unittest.main()
