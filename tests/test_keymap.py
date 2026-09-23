"""Shortcut parsing, normalisation and conflict detection."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import keymap  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_modifiers_and_key(self):
        self.assertEqual(keymap.parse("<Ctrl><Shift>T"), (["Ctrl", "Shift"], "T"))

    def test_lowercase_modifiers(self):
        self.assertEqual(keymap.parse("<ctrl><alt>Left"), (["Ctrl", "Alt"], "Left"))

    def test_bare_key(self):
        self.assertEqual(keymap.parse("F11"), ([], "F11"))

    def test_aliases(self):
        self.assertEqual(keymap.parse("<Mod1>PageUp"), (["Alt"], "Page_Up"))
        self.assertEqual(keymap.parse("<Ctrl>equal"), (["Ctrl"], "plus"))
        self.assertEqual(keymap.parse("<Ctrl>+"), (["Ctrl"], "plus"))
        self.assertEqual(keymap.parse("<Ctrl>comma"), (["Ctrl"], "comma"))

    def test_modifier_order_is_canonical(self):
        self.assertEqual(keymap.normalise("<Shift><Ctrl>T"), "<Ctrl><Shift>T")

    def test_duplicate_modifiers_collapse(self):
        self.assertEqual(keymap.normalise("<Ctrl><Ctrl>T"), "<Ctrl>T")

    def test_errors(self):
        for bad in ("", "<Ctrl", "<Foo>T", "<Ctrl>", "NotAKey"):
            with self.assertRaises(keymap.ShortcutError, msg=bad):
                keymap.parse(bad)

    def test_normalise_invalid_returns_empty(self):
        self.assertEqual(keymap.normalise("nonsense"), "")


class DisplayTests(unittest.TestCase):
    def test_display_is_human_readable(self):
        self.assertEqual(keymap.display("<Ctrl><Shift>T"), "Ctrl+Shift+T")
        self.assertEqual(keymap.display("<Ctrl>Page_Up"), "Ctrl+PgUp")
        self.assertEqual(keymap.display("<Ctrl>plus"), "Ctrl++")
        self.assertEqual(keymap.display("F11"), "F11")

    def test_display_passes_through_invalid(self):
        self.assertEqual(keymap.display("garbage"), "garbage")


class ConflictTests(unittest.TestCase):
    def test_detects_duplicates(self):
        conflicts = keymap.find_conflicts(
            {"a": "<Ctrl>T", "b": "<Ctrl>T", "c": "<Ctrl>Y"}
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][0], "<Ctrl>T")
        self.assertEqual({conflicts[0][1], conflicts[0][2]}, {"a", "b"})

    def test_ignores_invalid_entries(self):
        self.assertEqual(keymap.find_conflicts({"a": "junk", "b": ""}), [])

    def test_equivalent_spellings_conflict(self):
        conflicts = keymap.find_conflicts({"a": "<Ctrl>equal", "b": "<Ctrl>plus"})
        self.assertEqual(len(conflicts), 1)


class NamingTests(unittest.TestCase):
    def test_action_names_use_dashes(self):
        self.assertEqual(keymap.action_name("new_tab"), "new-tab")
        self.assertEqual(keymap.action_name("already-dashed"), "already-dashed")


if __name__ == "__main__":
    unittest.main()
