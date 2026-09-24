"""Path recognition for Ctrl+click, tested through the same regex VTE uses.

VTE's own ``match_check_event`` needs a realized terminal and a real pointer, so
the pattern is validated with Python's ``re`` (PCRE2 and Python agree on the
constructs used here) and the click handler is driven with a fake event.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela.terminal import PATH_PATTERN  # noqa: E402
from vela.terminal import _looks_like_path  # noqa: E402


class LooksLikePathTests(unittest.TestCase):
    """``_looks_like_path`` guards against inventing paths from ordinary text.

    Regression: the last line of the buffer used to be returned verbatim, so an
    idle prompt ``lpzone@gp76:~$`` became ``/home/lpzone/lpzone@gp76:~$``.
    """

    def test_shell_prompt_is_not_a_path(self):
        for prompt in (
            "lpzone@gp76:~$",
            "lpzone@gp76:~/vela-terminal$",
            "user@host:/etc#",
            "$",
            ">>>",
            "bash-5.0$",
        ):
            self.assertFalse(_looks_like_path(prompt), prompt)

    def test_absolute_and_relative_paths_are_paths(self):
        for value in (
            "/tmp/a.txt",
            "/var/log/syslog",
            "~/notes.md",
            "./build.sh",
            "../docs/plans/x.md",
            "~",
            "/",
        ):
            self.assertTrue(_looks_like_path(value), value)

    def test_plain_text_is_not_a_path(self):
        for value in ("hello world", "total 42", "", "   ", "a b c"):
            self.assertFalse(_looks_like_path(value), value)

    def test_quotes_are_tolerated(self):
        self.assertTrue(_looks_like_path("'/tmp/a.txt'"))
        self.assertTrue(_looks_like_path('"/tmp/a.txt"'))

    def test_windows_style_path_is_not_treated_as_posix(self):
        self.assertFalse(_looks_like_path(r"C:\Users\me\file.txt"))

    def test_absurdly_long_input_is_rejected(self):
        self.assertFalse(_looks_like_path("/" + "a" * 5000))

    def test_newlines_are_rejected(self):
        self.assertFalse(_looks_like_path("/tmp/a.txt\n/tmp/b.txt"))

    def test_bare_filename_is_not_path_shaped(self):
        # A bare name is not enough to claim the user pointed at a path; the
        # terminal's match regex handles those on Ctrl+click instead.
        self.assertFalse(_looks_like_path("notes.md"))


class PatternTests(unittest.TestCase):
    def setUp(self):
        self.pattern = re.compile(PATH_PATTERN)

    def match(self, text):
        found = self.pattern.search(text)
        return found.group(0) if found else None

    def test_absolute_path(self):
        self.assertEqual(
            self.match("/tmp/work/clickme.txt"), "/tmp/work/clickme.txt"
        )

    def test_relative_path_with_separator(self):
        self.assertEqual(self.match("cat src/main.c"), "src/main.c")
        self.assertEqual(self.match("run ./build.sh now"), "./build.sh")
        self.assertEqual(self.match("open ../docs/plans/x.md"), "../docs/plans/x.md")

    def test_home_relative_path(self):
        self.assertEqual(self.match("cat ~/a.py"), "~/a.py")
        self.assertEqual(self.match("vim ~/notes/todo.md"), "~/notes/todo.md")

    def test_bare_filename_with_extension(self):
        self.assertEqual(self.match("see a.png here"), "a.png")
        self.assertEqual(self.match("pyproject.toml"), "pyproject.toml")
        self.assertEqual(self.match("open notes.md"), "notes.md")

    def test_compound_archive_extension(self):
        self.assertEqual(self.match("tar -xzf archive.tar.gz"), "archive.tar.gz")

    def test_path_inside_a_sentence(self):
        self.assertEqual(
            self.match("error: failed at /var/log/syslog line 3"), "/var/log/syslog"
        )

    def test_url_is_not_a_path(self):
        self.assertIsNone(self.match("https://example.com/a.txt"))
        self.assertIsNone(self.match("ftp://host/dir/file.py"))

    def test_prompt_is_not_a_path(self):
        self.assertIsNone(self.match("lpzone@gp76:~/vela-terminal$"))

    def test_plain_words_are_ignored(self):
        self.assertIsNone(self.match("plain words here"))
        self.assertIsNone(self.match("echo done"))
        self.assertIsNone(self.match("total 42"))

    def test_unknown_extension_bare_name_is_ignored(self):
        self.assertIsNone(self.match("some randomword"))

    def test_directory_path_is_matched(self):
        self.assertEqual(
            self.match("vim /etc/nginx/nginx.conf"), "/etc/nginx/nginx.conf"
        )
        self.assertEqual(self.match("cd /usr/share/doc"), "/usr/share/doc")

    def test_trailing_slash_is_included_for_directories(self):
        self.assertEqual(self.match("ls /var/log/"), "/var/log/")

    def test_punctuation_around_path(self):
        self.assertEqual(self.match("see (/tmp/a.txt)"), "/tmp/a.txt")
        self.assertEqual(self.match("path: '/tmp/a.txt'."), "/tmp/a.txt")


class ClickHandlerTests(unittest.TestCase):
    """The handler must route matches to open-path and ignore everything else."""

    class FakeEvent:
        def __init__(self, button=1, control=True, shift=False, x=0, y=0):
            self.button = button
            self.x = x
            self.y = y
            self.state = 0
            if control:
                self.state |= 1 << 2  # Gdk.ModifierType.CONTROL_MASK
            if shift:
                self.state |= 1  # Gdk.ModifierType.SHIFT_MASK

    class FakeTerminal:
        def __init__(self, matched, char_width=8, char_height=17):
            self._matched = matched
            self.focused = False
            self._char_width = char_width
            self._char_height = char_height

        def match_check_event(self, _event):
            return self._matched

        def get_char_width(self):
            return self._char_width

        def get_char_height(self):
            return self._char_height

        def get_column_count(self):
            return 80

        def get_row_count(self):
            return 24

        def match_check(self, _column, _row):
            return self._matched

        def grab_focus(self):
            self.focused = True

    def make_view(self, matched, reveal_config=True):
        from vela.terminal import _MATCH_TAG_PATH

        class FakeConfig:
            def get(self, key):
                return reveal_config if key == "files.reveal_on_ctrl_shift_click" else True

        class FakeView:
            pass

        view = FakeView()
        view.config = FakeConfig()
        view.terminal = self.FakeTerminal(matched)
        view._path_match_installed = True
        view._path_match_id = 0
        view.emitted = []
        view.emit = lambda name, *args: view.emitted.append((name, args))
        view._MATCH_TAG_PATH = _MATCH_TAG_PATH
        # Bind the real implementation to the stand-in object.
        from vela.terminal import TerminalView

        view._on_button_press = TerminalView._on_button_press.__get__(view, FakeView)
        view._on_terminal_button_press = (
            TerminalView._on_terminal_button_press.__get__(view, FakeView)
        )
        view._path_at = TerminalView._path_at.__get__(view, FakeView)
        # The focus grab is deferred to an idle callback; the stand-in records it
        # instead of touching a real widget.
        view._claim_keyboard_focus = lambda: False
        # The menu itself is covered by test_terminal_menu; here it is stubbed so
        # the test only exercises which button reaches which code path.
        view._show_context_menu = lambda _event: None
        return view

    def test_ctrl_click_on_path_emits_open_path(self):
        from vela.terminal import _MATCH_TAG_PATH

        view = self.make_view(("/tmp/a.txt", _MATCH_TAG_PATH))
        handled = view._on_terminal_button_press(
            view, self.FakeEvent(control=True, x=20, y=8)
        )
        self.assertTrue(handled)
        self.assertEqual(view.emitted[-1][0], "open-path")
        self.assertEqual(view.emitted[-1][1], ("/tmp/a.txt", False))

    def test_ctrl_shift_click_requests_reveal(self):
        from vela.terminal import _MATCH_TAG_PATH

        view = self.make_view(("/tmp/a.txt", _MATCH_TAG_PATH))
        view._on_terminal_button_press(
            view, self.FakeEvent(control=True, shift=True, x=20, y=8)
        )
        self.assertTrue(view.emitted[-1][1][1])

    def test_plain_click_does_not_open(self):
        from vela.terminal import _MATCH_TAG_PATH

        view = self.make_view(("/tmp/a.txt", _MATCH_TAG_PATH))
        handled = view._on_terminal_button_press(
            view, self.FakeEvent(control=False, x=20, y=8)
        )
        self.assertFalse(handled)
        # A plain click must not open anything, but it does focus the pane (the
        # handler returns False so VTE still gets the click for selection).
        self.assertEqual([name for name, _args in view.emitted], ["focus-requested"])

    def test_other_match_tags_are_ignored(self):
        view = self.make_view(("http://example.com", 99))
        handled = view._on_terminal_button_press(
            view, self.FakeEvent(control=True, x=20, y=8)
        )
        self.assertFalse(handled)

    def test_no_match_does_not_open(self):
        view = self.make_view(None)
        handled = view._on_terminal_button_press(
            view, self.FakeEvent(control=True, x=20, y=8)
        )
        self.assertFalse(handled)

    def test_left_click_focuses_the_pane(self):
        """Regression: the left button used to be swallowed by VTE, so clicking
        another split did not move the focus."""
        view = self.make_view(None)
        view._on_terminal_button_press(
            view, self.FakeEvent(button=1, control=False, x=20, y=8)
        )
        self.assertIn("focus-requested", [name for name, _args in view.emitted])

    def test_right_click_focuses_and_opens_the_menu(self):
        view = self.make_view(None)
        handled = view._on_terminal_button_press(
            view, self.FakeEvent(button=3, control=False, x=20, y=8)
        )
        self.assertTrue(handled, "right click should be consumed by the menu")
        self.assertIn("focus-requested", [name for name, _args in view.emitted])

    def test_pane_frame_click_still_focuses(self):
        """Clicks on the padding around the terminal reach the pane itself."""
        view = self.make_view(None)
        view._on_button_press(view, self.FakeEvent(button=1, control=False))
        self.assertIn("focus-requested", [name for name, _args in view.emitted])

    def test_match_ids_starting_at_zero_still_work(self):
        """VTE hands out 0 as the first match id; that must not read as 'off'."""
        from vela.terminal import _MATCH_TAG_PATH

        view = self.make_view(("/tmp/a.txt", _MATCH_TAG_PATH))
        self.assertEqual(view._path_match_id, 0)
        self.assertTrue(view._path_match_installed)
        self.assertTrue(
            view._on_terminal_button_press(
                view, self.FakeEvent(control=True, x=20, y=8)
            )
        )

    def test_path_tag_is_the_group_number_vte_reports(self):
        """A pattern without capture groups reports tag 0; verify the constant."""
        from vela.terminal import PATH_PATTERN, _MATCH_TAG_PATH

        self.assertEqual(re.compile(PATH_PATTERN).groups, _MATCH_TAG_PATH)


if __name__ == "__main__":
    unittest.main()
