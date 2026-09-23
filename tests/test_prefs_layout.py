"""Preferences dialog layout.

Regression: the notebook inside the dialog did not expand, so every page was
squeezed into a ~184px strip at the top with empty space below it, and one long
hint label asked for 840px, which pushed the left-hand labels out of view.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from vela import config as config_mod  # noqa: E402
from vela import fonts as fonts_mod  # noqa: E402
from vela.prefs import PreferencesDialog  # noqa: E402


class FakeWindow(Gtk.Window):
    """Enough of MainWindow for the preferences dialog to build."""

    def __init__(self, config):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.config = config
        self.tabs = []
        self.theme = config.theme()

    def _refresh_css(self):
        pass

    def _apply_appearance(self):
        pass

    def apply_window_options(self):
        pass

    def show_status(self, *_args, **_kwargs):
        pass


def pump(times: int = 60) -> None:
    for _ in range(times):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


class DialogLayoutTests(unittest.TestCase):
    def setUp(self):
        self.window = FakeWindow(config_mod.Config())
        self.dialog = PreferencesDialog(self.window)
        self.dialog.set_default_size(560, 620)
        self.dialog.show_all()
        pump()
        self.notebook = self.dialog.get_content_area().get_children()[0]

    def tearDown(self):
        self.dialog.destroy()
        self.window.destroy()

    def test_notebook_expands_to_fill_the_dialog(self):
        allocated = self.notebook.get_allocated_height()
        dialog_height = self.dialog.get_allocated_height()
        self.assertGreater(dialog_height, 0)
        # Before the fix this was ~184px of a 620px dialog.
        self.assertGreater(
            allocated,
            dialog_height * 0.5,
            f"notebook only got {allocated}px of {dialog_height}px",
        )

    def test_notebook_is_marked_expanding(self):
        self.assertTrue(self.notebook.get_vexpand())
        self.assertTrue(self.notebook.get_hexpand())

    def test_every_page_fills_the_dialog(self):
        for index in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(index)
            title = self.notebook.get_tab_label(page).get_text()
            self.notebook.set_current_page(index)
            pump(20)
            with self.subTest(page=title):
                self.assertGreater(
                    page.get_allocated_height(),
                    self.dialog.get_allocated_height() * 0.5,
                    f"{title} 页只占用了 {page.get_allocated_height()}px",
                )

    def test_no_widget_overflows_its_page(self):
        """A too-wide row would push the labels off the left edge."""
        for index in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(index)
            title = self.notebook.get_tab_label(page).get_text()
            self.notebook.set_current_page(index)
            pump(20)
            limit = page.get_allocation().x + page.get_allocation().width
            worst = 0

            def scan(widget):
                nonlocal worst
                if not isinstance(widget, Gtk.Container):
                    return
                for child in widget.get_children():
                    allocation = child.get_allocation()
                    if allocation.x + allocation.width > limit + 1:
                        worst = max(worst, allocation.x + allocation.width - limit)
                    scan(child)

            scan(page)
            with self.subTest(page=title):
                self.assertEqual(worst, 0, f"{title} 页有 {worst}px 内容溢出")

    def test_page_count_and_titles(self):
        titles = [
            self.notebook.get_tab_label(self.notebook.get_nth_page(i)).get_text()
            for i in range(self.notebook.get_n_pages())
        ]
        self.assertEqual(
            titles, ["外观", "字体", "行为", "窗口", "面板与文件", "快捷键"]
        )


class FontPageTests(unittest.TestCase):
    def setUp(self):
        self.window = FakeWindow(config_mod.Config())
        self.dialog = PreferencesDialog(self.window)
        self.dialog.show_all()
        pump()

    def tearDown(self):
        self.dialog.destroy()
        self.window.destroy()

    def test_picker_is_populated_from_installed_fonts(self):
        model = self.dialog._family_combo.get_model()
        entries = [row[0] for row in model] if model is not None else []
        installed = fonts_mod.monospace_families()
        if not installed:
            self.skipTest("Pango 不可用")
        for family in installed:
            self.assertIn(family, entries)

    def test_picker_selects_the_configured_font(self):
        configured = self.window.config.get("appearance.font_family")
        self.assertEqual(self.dialog._family_combo.get_active_text(), configured)

    def test_preview_uses_the_configured_font(self):
        configured = self.window.config.get("appearance.font_family")
        self.assertIn(configured, self.dialog._preview.get_text())

    def test_download_buttons_match_the_catalog(self):
        self.assertEqual(
            set(self.dialog._download_buttons), {c.key for c in fonts_mod.catalog()}
        )

    def test_already_installed_fonts_are_disabled(self):
        for key in fonts_mod.installed_catalog_keys():
            button = self.dialog._download_buttons.get(key)
            if button is not None:
                with self.subTest(font=key):
                    self.assertFalse(button.get_sensitive())
                    self.assertEqual(button.get_label(), "已安装")

    def test_download_buttons_keep_their_natural_width(self):
        for key, button in self.dialog._download_buttons.items():
            with self.subTest(font=key):
                self.assertEqual(button.get_halign(), Gtk.Align.START)

    def test_rescan_keeps_the_selection(self):
        before = self.dialog._family_combo.get_active_text()
        self.dialog._rescan_fonts()
        pump(20)
        self.assertEqual(self.dialog._family_combo.get_active_text(), before)


class FontFallbackConfigTests(unittest.TestCase):
    def test_fallback_defaults_to_empty(self):
        self.assertEqual(config_mod.Config().get("appearance.fallback_font"), "")

    def test_fallback_is_trimmed(self):
        config = config_mod.Config({"appearance": {"fallback_font": "  A B  "}})
        self.assertEqual(config.get("appearance.fallback_font"), "A B")

    def test_fallback_round_trips(self):
        config = config_mod.Config()
        config.set("appearance.fallback_font", "Noto Sans Mono CJK SC")
        text = config.to_toml()
        self.assertIn("Noto Sans Mono CJK SC", text)
        reloaded = config_mod.Config(config_mod.minitoml.loads(text))
        self.assertEqual(
            reloaded.get("appearance.fallback_font"), "Noto Sans Mono CJK SC"
        )


if __name__ == "__main__":
    unittest.main()
