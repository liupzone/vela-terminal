"""Reading the shell working directory for the panels that follow it.

VTE reports a directory only when the shell runs its integration hook, which a
non-login shell never does, so the reported URI stays ``None`` on a default
setup and anything following the directory would never move.  ``refresh_directory``
reads ``/proc/<pid>/cwd`` instead; these tests pin down the fallbacks so the
behaviour does not depend on the machine's shell configuration.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela.terminal import TerminalView  # noqa: E402


class _FakeView:
    """Just the attributes ``refresh_directory`` reads."""

    refresh_directory = TerminalView.refresh_directory

    def __init__(self, pid: int = 0, directory: str = "") -> None:
        self._child_pid = pid
        self._directory = directory
        self.emitted = []

    @property
    def child_pid(self) -> int:
        return self._child_pid

    def emit(self, name, path) -> None:
        self.emitted.append((name, path))


class ProcDirectoryTests(unittest.TestCase):
    def test_reads_the_live_directory(self):
        view = _FakeView(pid=os.getpid())
        self.assertEqual(view.refresh_directory(), os.getcwd())

    def test_emits_the_change_once(self):
        view = _FakeView(pid=os.getpid())
        view.refresh_directory()
        self.assertEqual(view.emitted, [("directory-changed", os.getcwd())])
        # Reading again with no change must not re-emit.
        view.refresh_directory()
        self.assertEqual(len(view.emitted), 1)

    def test_a_real_move_is_reported(self):
        view = _FakeView(pid=os.getpid())
        view.refresh_directory()
        view.emitted.clear()
        with tempfile.TemporaryDirectory() as target:
            previous = os.getcwd()
            os.chdir(target)
            try:
                real = os.path.realpath(target)
                self.assertEqual(view.refresh_directory(), real)
                self.assertEqual(view.emitted, [("directory-changed", real)])
            finally:
                os.chdir(previous)


class FallbackTests(unittest.TestCase):
    def test_no_pid_falls_back_to_the_last_reported_directory(self):
        view = _FakeView(pid=0, directory="/tmp")
        self.assertEqual(view.refresh_directory(), "/tmp")

    def test_no_pid_and_no_history_is_empty(self):
        self.assertEqual(_FakeView(pid=0).refresh_directory(), "")

    def test_a_dead_pid_falls_back(self):
        # PID 0x7FFFFFFF is not a running process, so readlink fails.
        view = _FakeView(pid=0x7FFFFFFF, directory="/tmp")
        self.assertEqual(view.refresh_directory(), "/tmp")

    def test_a_stale_directory_is_not_reported_as_a_move(self):
        """The fallback must not re-emit when nothing changed."""
        view = _FakeView(pid=0, directory="/tmp")
        view.refresh_directory()
        view.refresh_directory()
        self.assertEqual(view.emitted, [])


if __name__ == "__main__":
    unittest.main()
