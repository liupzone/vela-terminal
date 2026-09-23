"""Tests for the dependency-free TOML subset reader/writer."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import minitoml  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_scalars(self):
        data = minitoml.loads(
            """
            name = "vela"
            size = 12
            scale = 1.5
            dark = true
            off = false
            """
        )
        self.assertEqual(data["name"], "vela")
        self.assertEqual(data["size"], 12)
        self.assertEqual(data["scale"], 1.5)
        self.assertIs(data["dark"], True)
        self.assertIs(data["off"], False)

    def test_sections_and_nesting(self):
        data = minitoml.loads(
            """
            [appearance]
            theme = "nord"

            [appearance.window]
            width = 800
            """
        )
        self.assertEqual(data["appearance"]["theme"], "nord")
        self.assertEqual(data["appearance"]["window"]["width"], 800)

    def test_arrays(self):
        data = minitoml.loads('items = ["a", "b"]\nnums = [1, 2, 3]\nempty = []')
        self.assertEqual(data["items"], ["a", "b"])
        self.assertEqual(data["nums"], [1, 2, 3])
        self.assertEqual(data["empty"], [])

    def test_nested_array_in_value(self):
        data = minitoml.loads('matrix = [["a", "b"], ["c"]]')
        self.assertEqual(data["matrix"], [["a", "b"], ["c"]])

    def test_comments_and_blank_lines(self):
        data = minitoml.loads(
            """
            # leading comment
            value = 1  # trailing comment

            # another
            other = "#not-a-comment"
            """
        )
        self.assertEqual(data["value"], 1)
        self.assertEqual(data["other"], "#not-a-comment")

    def test_quoted_keys(self):
        data = minitoml.loads('"weird key" = 1')
        self.assertEqual(data["weird key"], 1)

    def test_escapes(self):
        data = minitoml.loads(r'path = "C:\\tmp\\n"')
        # TOML "\\" is an escaped backslash, so this is a literal backslash-n.
        self.assertEqual(data["path"], "C:\\tmp\\n")
        self.assertEqual(minitoml.loads(r'v = "a\nb"')["v"], "a\nb")

    def test_literal_string(self):
        data = minitoml.loads(r"raw = 'a\b'")
        self.assertEqual(data["raw"], r"a\b")

    def test_errors_report_line_numbers(self):
        with self.assertRaises(minitoml.TomlError) as context:
            minitoml.loads("ok = 1\nbroken line\n")
        self.assertEqual(context.exception.line_no, 2)

    def test_unknown_value_type_is_rejected(self):
        with self.assertRaises(minitoml.TomlError):
            minitoml.loads("date = 2026-09-22")

    def test_unterminated_string_is_rejected(self):
        with self.assertRaises(minitoml.TomlError):
            minitoml.loads('name = "vela')

    def test_table_conflict_is_rejected(self):
        with self.assertRaises(minitoml.TomlError):
            minitoml.loads('a = 1\n[a]\nb = 2\n')


class DumpTests(unittest.TestCase):
    def test_round_trip(self):
        original = {
            "appearance": {"theme": "nord", "font_size": 12.0, "bold": True},
            "window": {"width": 800},
            "lists": {"items": ["a", "b"]},
        }
        text = minitoml.dumps(original)
        self.assertEqual(minitoml.loads(text), original)

    def test_header_is_rendered_as_comments(self):
        text = minitoml.dumps({"a": 1}, "hello\nworld")
        self.assertIn("# hello", text)
        self.assertIn("# world", text)
        self.assertEqual(minitoml.loads(text), {"a": 1})

    def test_string_escaping(self):
        text = minitoml.dumps({"path": 'a"b\\c\nd'})
        self.assertEqual(minitoml.loads(text)["path"], 'a"b\\c\nd')

    def test_unsupported_type_raises(self):
        with self.assertRaises(TypeError):
            minitoml.dumps({"bad": object()})


if __name__ == "__main__":
    unittest.main()
