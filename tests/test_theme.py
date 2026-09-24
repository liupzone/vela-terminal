"""Theme integrity: every theme must be complete and self-consistent."""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import style, theme as theme_mod  # noqa: E402

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class ThemeTests(unittest.TestCase):
    def test_theme_count_grew(self):
        """The palette set should stay generous."""
        self.assertGreaterEqual(len(theme_mod.names()), 30)

    def test_every_theme_has_a_distinct_name_and_label(self):
        names = theme_mod.names()
        self.assertEqual(len(names), len(set(names)))
        labels = [theme_mod.label(name) for name in names]
        self.assertEqual(len(labels), len(set(labels)))

    def test_background_hues_are_varied(self):
        """The set must not be dominated by one hue.

        The original palettes were almost all blue/purple (hue 216-250), so this
        guards against new themes collapsing back into a single colour family.
        """
        import colorsys

        def hue_of(color):
            value = color.lstrip("#")
            red, green, blue = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
            return colorsys.rgb_to_hsv(red, green, blue)[0] * 360

        def bucket(hue):
            if hue < 40 or hue >= 340:
                return "warm"
            if hue < 70:
                return "yellow"
            if hue < 160:
                return "green"
            if hue < 200:
                return "cyan"
            if hue < 260:
                return "blue"
            return "purple"

        counts = {}
        for name in theme_mod.names():
            key = bucket(hue_of(theme_mod.get(name).background))
            counts[key] = counts.get(key, 0) + 1

        # Warm, green and cyan backgrounds must be represented, not just blue.
        for family in ("warm", "green", "cyan"):
            with self.subTest(family=family):
                self.assertGreaterEqual(
                    counts.get(family, 0), 2,
                    f"{family} 色系主题太少：{counts}",
                )
        # And no single family may dominate the whole set.
        total = sum(counts.values())
        self.assertLess(
            max(counts.values()) / total, 0.75,
            f"色相过于单一：{counts}",
        )

    def test_both_dark_and_light_themes_are_plentiful(self):
        dark = sum(1 for name in theme_mod.names() if theme_mod.get(name).dark)
        light = len(theme_mod.names()) - dark
        self.assertGreaterEqual(dark, 20, f"深色主题只有 {dark} 套")
        self.assertGreaterEqual(light, 8, f"浅色主题只有 {light} 套")

    def test_contrast_is_readable(self):
        """Foreground text must stand out from the background."""
        def luminance(color):
            value = color.lstrip("#")
            channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            linear = [
                c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                for c in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        def contrast(a, b):
            la, lb = luminance(a), luminance(b)
            lighter, darker = max(la, lb), min(la, lb)
            return (lighter + 0.05) / (darker + 0.05)

        for name in theme_mod.names():
            theme = theme_mod.get(name)
            with self.subTest(theme=name):
                ratio = contrast(theme.foreground, theme.background)
                self.assertGreater(
                    ratio, 4.5,
                    f"{name}: 前景/背景对比度只有 {ratio:.1f}:1，文字会难以阅读",
                )

    def test_ansi_colours_stay_distinguishable(self):
        """The ANSI slots must stay visually distinct from one another.

        A monochrome theme (amber CRT, green CRT) is easy to over-tint: the first
        attempt made ``ls --color`` output nearly uniform, which defeats the
        point of a 16-colour palette.  The check is on *distinctness* rather than
        on specific hues, because several upstream palettes deliberately use
        off-hue colours (Monokai Pro's blue slot is orange).
        """
        def distance(a, b):
            left = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
            right = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
            return sum((x - y) ** 2 for x, y in zip(left, right)) ** 0.5

        # Two colours closer than this are hard to tell apart on screen.
        minimum = 40.0
        for name in theme_mod.names():
            theme = theme_mod.get(name)
            palette = theme.palette
            with self.subTest(theme=name):
                for first, second in (
                    ("red", "green"),
                    ("red", "blue"),
                    ("green", "blue"),
                    ("red", "yellow"),
                    ("magenta", "cyan"),
                ):
                    gap = distance(palette[first], palette[second])
                    self.assertGreater(
                        gap, minimum,
                        f"{name}: ANSI {first} 与 {second} 太接近（距离 {gap:.0f}）",
                    )

    def test_selection_text_is_readable_on_selection(self):
        def luminance(color):
            value = color.lstrip("#")
            channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            linear = [
                c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                for c in channels
            ]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        for name in theme_mod.names():
            theme = theme_mod.get(name)
            la, lb = luminance(theme.selection_text), luminance(theme.selection)
            ratio = (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
            with self.subTest(theme=name):
                self.assertGreater(
                    ratio, 3.0,
                    f"{name}: 选中文字对比度只有 {ratio:.1f}:1",
                )

    def test_expected_themes_exist(self):
        for name in (
            "vela-dark",
            "vela-light",
            "dracula",
            "nord",
            "tokyo-night",
            "catppuccin-mocha",
            "gruvbox-dark",
            "solarized-dark",
            "solarized-light",
            "one-dark",
        ):
            self.assertIn(name, theme_mod.names(), name)

    def test_palette_is_complete_and_valid(self):
        for name in theme_mod.names():
            theme = theme_mod.get(name)
            palette = theme.ansi()
            self.assertEqual(len(palette), 16, name)
            for color in palette:
                self.assertRegex(color, HEX, f"{name} palette {color}")
            for key in ("background", "foreground", "cursor", "selection", "accent"):
                self.assertRegex(
                    theme.color(key), HEX, f"{name} {key}"
                )

    def test_ui_colors_are_valid(self):
        for name in theme_mod.names():
            theme = theme_mod.get(name)
            for key, value in theme.ui.items():
                self.assertRegex(value, HEX, f"{name} ui.{key}")

    def test_auto_theme_resolves_to_a_real_theme(self):
        resolved = theme_mod.get(theme_mod.AUTO_THEME)
        self.assertIn(resolved.name, theme_mod.names())
        self.assertIn(theme_mod.AUTO_THEME, theme_mod.all_names())

    def test_unknown_theme_falls_back(self):
        self.assertEqual(
            theme_mod.get("nope").name, theme_mod.DEFAULT_THEME
        )

    def test_labels_are_unique(self):
        labels = [theme_mod.label(name) for name in theme_mod.all_names()]
        self.assertEqual(len(labels), len(set(labels)))

    def test_css_is_generated_for_every_theme(self):
        for name in theme_mod.all_names():
            theme = theme_mod.get(name)
            css = style.build_css(theme)
            self.assertIn(theme.background, css)
            self.assertIn(".vela-pane", css)
            # No unformatted f-string placeholders may survive templating.
            for leftover in ("{{", "}}", "{theme.", "{_alpha(", "{ui("):
                self.assertNotIn(leftover, css, f"{name} has {leftover}")
            self.assertIn(theme.accent, css)

    def test_to_dict_is_serialisable(self):
        for name in theme_mod.names():
            data = theme_mod.get(name).to_dict()
            self.assertIsInstance(data["dark"], bool)
            for key in theme_mod.ANSI_NAMES:
                self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
