"""Tests for configuration loading, validation and persistence."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import config as config_mod  # noqa: E402
from vela import theme as theme_mod  # noqa: E402


class ConfigDefaultsTests(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            config = config_mod.Config.load(os.path.join(directory, "missing.toml"))
            self.assertEqual(config.problems, [])
            self.assertEqual(
                config.get("appearance.theme"), theme_mod.DEFAULT_THEME
            )
            self.assertEqual(config.get("window.width"), 1080)
            self.assertIn("new_tab", config.keybindings())

    def test_broken_file_falls_back_with_problem(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("this is not toml\n")
            config = config_mod.Config.load(path)
            self.assertTrue(config.problems)
            self.assertEqual(
                config.get("appearance.theme"), theme_mod.DEFAULT_THEME
            )

    def test_unknown_theme_falls_back(self):
        config = config_mod.Config({"appearance": {"theme": "does-not-exist"}})
        self.assertEqual(config.get("appearance.theme"), theme_mod.DEFAULT_THEME)
        self.assertTrue(any("主题" in problem for problem in config.problems))


class ValidationTests(unittest.TestCase):
    def test_numeric_clamping(self):
        config = config_mod.Config(
            {
                "appearance": {"font_size": 999, "scrollback_lines": -5, "opacity": 5},
                "window": {"width": 10},
            }
        )
        self.assertEqual(config.get("appearance.font_size"), 72.0)
        self.assertEqual(config.get("appearance.scrollback_lines"), 0)
        self.assertEqual(config.get("appearance.opacity"), 1.0)
        self.assertEqual(config.get("window.width"), 320)

    def test_choice_fields(self):
        config = config_mod.Config(
            {
                "appearance": {"cursor_shape": "triangle", "cursor_blink": "maybe"},
                "window": {"tab_position": "left", "show_tabbar": "sometimes"},
            }
        )
        self.assertEqual(config.get("appearance.cursor_shape"), "block")
        self.assertEqual(config.get("appearance.cursor_blink"), "system")
        self.assertEqual(config.get("window.tab_position"), "top")
        self.assertEqual(config.get("window.show_tabbar"), "always")

    def test_string_booleans_are_accepted(self):
        config = config_mod.Config({"behavior": {"copy_on_select": "yes"}})
        self.assertIs(config.get("behavior.copy_on_select"), True)

    def test_empty_word_chars_falls_back(self):
        config = config_mod.Config({"behavior": {"word_char_exceptions": ""}})
        self.assertTrue(config.get("behavior.word_char_exceptions"))

    def test_unknown_keys_are_preserved(self):
        config = config_mod.Config({"custom": {"keep": "me"}})
        self.assertEqual(config.get("custom.keep"), "me")


class PersistenceTests(unittest.TestCase):
    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "nested", "config.toml")
            config = config_mod.Config.load(path)
            config.set("appearance.theme", "nord")
            config.set("appearance.font_size", 15.0)
            config.set("window.show_statusbar", False)
            config.save()
            self.assertTrue(os.path.exists(path))

            reloaded = config_mod.Config.load(path)
            self.assertEqual(reloaded.problems, [])
            self.assertEqual(reloaded.get("appearance.theme"), "nord")
            self.assertEqual(reloaded.get("appearance.font_size"), 15.0)
            self.assertIs(reloaded.get("window.show_statusbar"), False)
            self.assertEqual(reloaded.keybindings(), config.keybindings())

    def test_save_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.toml")
            config = config_mod.Config.load(path)
            config.save()
            first = os.path.getmtime(path)
            config.save()
            self.assertEqual(os.path.getmtime(path), first)

    def test_reload_picks_up_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.toml")
            config = config_mod.Config.load(path)
            config.set("appearance.theme", "dracula")
            config.save()
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n[appearance]\ntheme = \"gruvbox-dark\"\n")
            config.reload()
            self.assertEqual(config.get("appearance.theme"), "gruvbox-dark")

    def test_flattened_contains_dotted_keys(self):
        config = config_mod.Config()
        keys = dict(config.flattened())
        self.assertIn("appearance.theme", keys)
        self.assertIn("keybindings.new_tab", keys)


class PathTests(unittest.TestCase):
    def test_config_dir_override(self):
        previous = os.environ.get(config_mod.CONFIG_DIR_ENV)
        os.environ[config_mod.CONFIG_DIR_ENV] = "/tmp/vela-test-config"
        try:
            self.assertEqual(config_mod.config_home(), "/tmp/vela-test-config")
            self.assertTrue(
                config_mod.config_path().endswith("vela-test-config/config.toml")
            )
        finally:
            if previous is None:
                os.environ.pop(config_mod.CONFIG_DIR_ENV, None)
            else:
                os.environ[config_mod.CONFIG_DIR_ENV] = previous


class ObsoleteKeyTests(unittest.TestCase):
    """Superseded keys are dropped and the user is told where the setting went."""

    def test_side_panel_keys_are_removed(self):
        config = config_mod.Config(
            {"sysinfo": {"side": "left", "width": 400}, "filebrowser": {"width": 350}}
        )
        self.assertNotIn("side", config.section("sysinfo"))
        self.assertNotIn("width", config.section("sysinfo"))
        self.assertNotIn("width", config.section("filebrowser"))
        self.assertTrue(any("已废弃" in problem for problem in config.problems))

    def test_sidebar_width_is_kept(self):
        config = config_mod.Config({"window": {"sidebar_width": 420}})
        self.assertEqual(config.get("window.sidebar_width"), 420)
        self.assertEqual(config.problems, [])

    def test_obsolete_keys_do_not_come_back_after_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.toml")
            config = config_mod.Config(
                {"sysinfo": {"side": "left", "width": 400}}, path
            )
            config.save()
            reloaded = config_mod.Config.load(path)
            self.assertNotIn("side", reloaded.section("sysinfo"))
            self.assertNotIn("width", reloaded.section("sysinfo"))


if __name__ == "__main__":
    unittest.main()
