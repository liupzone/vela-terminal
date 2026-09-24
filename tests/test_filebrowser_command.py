"""The file panel command bar.

The bar is the one part of the panel that reaches outside it: it hands the text
to the window, which types it into the tab terminal.  What is worth testing here
is the part the panel owns: that it does not invent commands, that the history
behaves like a shell history, and that a panel which cannot run a command says
so instead of silently swallowing it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from vela.filebrowser import FileBrowserPanel, parse_cd  # noqa: E402


class _Entry:
    """Stand-in for Gtk.Entry: the panel only uses these four calls."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text

    def set_position(self, _position: int) -> None:
        pass

    def grab_focus(self) -> None:
        pass


class _Key:
    def __init__(self, keyval: int) -> None:
        self.keyval = keyval


def make_panel():
    """A panel with just the state the command bar touches."""
    panel = FileBrowserPanel.__new__(FileBrowserPanel)
    panel.command_entry = _Entry()
    panel._command_history = []
    panel._history_index = None
    panel._notify = lambda *_args: None
    panel.directory = ""
    panel._previous_directory = ""
    return panel


class ParseCdTests(unittest.TestCase):
    """The bar is a directory jump box, not a shell."""

    def test_plain_cd(self):
        self.assertEqual(parse_cd("cd /tmp"), "/tmp")

    def test_bare_path_is_accepted(self):
        self.assertEqual(parse_cd("/tmp"), "/tmp")

    def test_cd_alone_means_home(self):
        self.assertEqual(parse_cd("cd"), "~")

    def test_previous_directory(self):
        self.assertEqual(parse_cd("cd -"), "-")

    def test_relative_path(self):
        self.assertEqual(parse_cd("cd .."), "..")

    def test_double_quoted_path_with_spaces(self):
        self.assertEqual(parse_cd('cd "/a b"'), "/a b")

    def test_single_quoted_path_with_spaces(self):
        self.assertEqual(parse_cd("cd '/a b'"), "/a b")

    def test_extra_arguments_are_rejected(self):
        self.assertEqual(parse_cd("cd /a /b"), None)

    def test_other_commands_are_rejected(self):
        for text in ("ls", "rm -rf /", "echo hi", "git status"):
            self.assertIsNone(parse_cd(text), text)

    def test_unterminated_quote_is_rejected(self):
        self.assertIsNone(parse_cd('cd "unterminated'))

    def test_blank_is_rejected(self):
        for text in ("", "   ", "\t"):
            self.assertIsNone(parse_cd(text), repr(text))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_cd("  cd   /tmp  "), "/tmp")


class RunCommandTests(unittest.TestCase):
    def make(self, directory="/tmp"):
        panel = make_panel()
        panel.directory = directory
        panel._previous_directory = ""
        panel.navigated = []
        panel.navigate = lambda path: panel.navigated.append(path) or True
        return panel

    def test_a_cd_navigates_the_panel(self):
        panel = self.make()
        panel.command_entry.text = "cd /usr"
        panel.run_command()
        self.assertEqual(panel.navigated, ["/usr"])

    def test_a_bare_path_navigates(self):
        panel = self.make()
        panel.command_entry.text = "/usr"
        panel.run_command()
        self.assertEqual(panel.navigated, ["/usr"])

    def test_a_relative_path_resolves_against_the_panel(self):
        panel = self.make(directory="/usr")
        panel.command_entry.text = "cd share"
        panel.run_command()
        self.assertEqual(panel.navigated, ["/usr/share"])

    def test_tilde_is_expanded(self):
        panel = self.make()
        panel.command_entry.text = "cd ~"
        panel.run_command()
        self.assertEqual(panel.navigated, [os.path.expanduser("~")])

    def test_previous_directory_is_remembered(self):
        panel = self.make(directory="/usr")
        panel._previous_directory = "/etc"
        panel.command_entry.text = "cd -"
        panel.run_command()
        self.assertEqual(panel.navigated, ["/etc"])

    def test_a_missing_directory_is_reported_and_not_navigated(self):
        panel = self.make()
        messages = []
        panel._notify = lambda message, *args: messages.append(message)
        panel.command_entry.text = "cd /no/such/dir-vela"
        panel.run_command()
        self.assertEqual(panel.navigated, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("不存在", messages[0])

    def test_a_file_is_reported_and_not_navigated(self):
        panel = self.make()
        messages = []
        panel._notify = lambda message, *args: messages.append(message)
        panel.command_entry.text = "cd /etc/hostname"
        panel.run_command()
        self.assertEqual(panel.navigated, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("不是目录", messages[0])

    def test_other_commands_do_not_navigate(self):
        panel = self.make()
        messages = []
        panel._notify = lambda message, *args: messages.append(message)
        for text in ("ls", "rm -rf /", "echo hi"):
            panel.command_entry.text = text
            panel.run_command()
        self.assertEqual(panel.navigated, [])
        self.assertEqual(len(messages), 3)
        self.assertIn("只支持 cd", messages[0])

    def test_entry_is_cleared_after_navigating(self):
        panel = self.make()
        panel.command_entry.text = "cd /usr"
        panel.run_command()
        self.assertEqual(panel.command_entry.get_text(), "")

    def test_blank_command_is_ignored(self):
        panel = self.make()
        for blank in ("", "   ", "\t"):
            panel.command_entry.text = blank
            panel.run_command()
        self.assertEqual(panel.navigated, [])

    def test_a_rejected_command_stays_in_the_entry(self):
        """The user must be able to fix a typo instead of retyping it."""
        panel = self.make()
        panel.command_entry.text = "cd /no/such/dir-vela"
        panel.run_command()
        self.assertEqual(panel.command_entry.get_text(), "cd /no/such/dir-vela")

    def test_command_is_remembered(self):
        panel = self.make()
        panel.command_entry.text = "cd /usr"
        panel.run_command()
        self.assertEqual(panel._command_history, ["cd /usr"])


class HistoryTests(unittest.TestCase):
    def press(self, panel, keyval):
        return panel._on_command_key(None, _Key(keyval))

    def test_up_recalls_the_last_command(self):
        panel = make_panel()
        panel._command_history = ["ls", "cd /tmp"]
        self.assertTrue(self.press(panel, Gdk.KEY_Up))
        self.assertEqual(panel.command_entry.get_text(), "cd /tmp")

    def test_up_walks_backwards(self):
        panel = make_panel()
        panel._command_history = ["ls", "cd /tmp", "pwd"]
        self.press(panel, Gdk.KEY_Up)
        self.press(panel, Gdk.KEY_Up)
        self.assertEqual(panel.command_entry.get_text(), "cd /tmp")
        self.press(panel, Gdk.KEY_Up)
        self.assertEqual(panel.command_entry.get_text(), "ls")

    def test_up_stops_at_the_oldest_command(self):
        panel = make_panel()
        panel._command_history = ["ls"]
        for _ in range(4):
            self.press(panel, Gdk.KEY_Up)
        self.assertEqual(panel.command_entry.get_text(), "ls")

    def test_down_returns_to_the_draft(self):
        panel = make_panel()
        panel._command_history = ["ls", "cd /tmp"]
        self.press(panel, Gdk.KEY_Up)
        self.press(panel, Gdk.KEY_Down)
        self.assertEqual(panel.command_entry.get_text(), "")

    def test_down_without_recall_is_consumed(self):
        """A bare Down must not move the cursor out of an empty entry."""
        panel = make_panel()
        panel._command_history = ["ls"]
        self.assertTrue(self.press(panel, Gdk.KEY_Down))
        self.assertEqual(panel.command_entry.get_text(), "")

    def test_history_is_untouched_by_recall(self):
        panel = make_panel()
        panel._command_history = ["ls", "cd /tmp"]
        self.press(panel, Gdk.KEY_Up)
        self.assertEqual(panel._command_history, ["ls", "cd /tmp"])

    def test_other_keys_are_left_to_the_entry(self):
        panel = make_panel()
        panel._command_history = ["ls"]
        self.assertFalse(self.press(panel, Gdk.KEY_Return))
        self.assertFalse(self.press(panel, Gdk.KEY_a))

    def test_running_resets_the_history_cursor(self):
        panel = make_panel()
        panel.directory = "/tmp"
        panel.navigate = lambda path: True
        panel._command_history = ["cd /usr"]
        self.press(panel, Gdk.KEY_Up)
        panel.command_entry.text = "cd /etc"
        panel.run_command()
        # The next Up must start from the newest entry again.
        self.press(panel, Gdk.KEY_Up)
        self.assertEqual(panel.command_entry.get_text(), "cd /etc")


class _FakeConfig:
    def get(self, key):
        return {
            "filebrowser.show_hidden": False,
            "filebrowser.max_entries": 100,
            "filebrowser.directories_first": True,
            "filebrowser.icon_size": 16,
        }.get(key)


class ConstructionTests(unittest.TestCase):
    """The bar has to be reachable and themed like the rest of the panel."""

    def test_bar_is_built_under_the_listing(self):
        panel = FileBrowserPanel.__new__(FileBrowserPanel)
        panel.config = _FakeConfig()
        panel.theme = None
        panel._on_open = None
        panel._notify = lambda *_args: None
        panel.directory = ""
        panel._previous_directory = ""
        panel.following = True
        panel.show_hidden = False
        panel._icon_cache = {}
        panel._up_row = None
        Gtk.Box.__init__(panel, orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel._build()
        children = panel.get_children()
        self.assertIsInstance(panel.command_entry, Gtk.Entry)
        bar = panel.command_entry.get_parent()
        self.assertIn(bar, children)
        # The command bar is the last thing in the panel.
        self.assertIs(children[-1], bar)
        self.assertTrue(bar.get_style_context().has_class("vela-command-bar"))


if __name__ == "__main__":
    unittest.main()
