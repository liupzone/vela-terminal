"""Typing ``exit`` in a pane: pane -> tab -> window, and the abnormal case.

The decision logic lives in ``MainWindow.pane_exited``; these tests drive it with
stand-in tabs/views so no display or real shell is needed.  The GTK-level effects
(pane actually disappearing) are covered by the smoke test.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vela import config as config_mod  # noqa: E402
from vela.window import _clean_exit, _exit_code, _exit_label  # noqa: E402


class ExitCodeTests(unittest.TestCase):
    def test_clean_exit(self):
        self.assertEqual(_exit_code(0), 0)
        self.assertTrue(_clean_exit(0))

    def test_nonzero_exit(self):
        self.assertEqual(_exit_code(1 << 8), 1)
        self.assertEqual(_exit_code(3 << 8), 3)
        self.assertFalse(_clean_exit(3 << 8))

    def test_signal_death_is_not_clean(self):
        # 15 == SIGTERM
        status = 15
        self.assertEqual(_exit_code(status), 128 + 15)
        self.assertFalse(_clean_exit(status))

    def test_unknown_status(self):
        self.assertIsNone(_exit_code(None))
        self.assertFalse(_clean_exit(None))
        self.assertIn("未知", _exit_label(None))

    def test_labels_are_readable(self):
        self.assertIn("0", _exit_label(0))
        self.assertIn("3", _exit_label(3 << 8))
        self.assertIn("信号", _exit_label(15))


class FakeView:
    def __init__(self, status=0):
        self.exited = True
        self.exit_status = status


class FakeLeaf:
    def __init__(self, view):
        self.view = view


class FakeContainer:
    def __init__(self, views):
        self.leaves_list = [FakeLeaf(v) for v in views]
        self.active = self.leaves_list[0]

    def leaves(self):
        return list(self.leaves_list)

    def count(self):
        return len(self.leaves_list)

    def close(self, leaf):
        self.leaves_list.remove(leaf)
        if self.active is leaf:
            self.active = self.leaves_list[0] if self.leaves_list else None

    def set_active(self, leaf):
        self.active = leaf


class FakeTab:
    def __init__(self, views):
        self.container = FakeContainer(views)
        self.title = ""
        self.refreshed = 0

    def _leaf_for(self, view):
        for leaf in self.container.leaves():
            if leaf.view is view:
                return leaf
        return None

    def refresh_title(self):
        self.refreshed += 1


class PaneExitTests(unittest.TestCase):
    """Exercise MainWindow.pane_exited with a minimal window stand-in."""

    def make_window(self, overrides=None):
        from vela.window import MainWindow

        settings = {
            "behavior.close_pane_on_exit": True,
            "behavior.close_window_on_last_exit": True,
            "behavior.close_on_abnormal_exit": False,
        }
        settings.update(overrides or {})

        class FakeConfig:
            def get(self, key, fallback=None):
                return settings.get(key, fallback)

        class FakeWindow:
            pass

        window = FakeWindow()
        window.config = FakeConfig()
        window.tabs = []
        window.closed_tabs = []
        window.statuses = []
        window.window_exits = 0
        window.show_status = lambda msg, *a, **k: window.statuses.append(msg)
        window.update_statusbar = lambda: None
        window.refresh_window_title = lambda: None
        window.close_tab = (
            lambda tab, force=False, from_shell_exit=False: window.closed_tabs.append(
                (tab, from_shell_exit)
            )
        )
        window.window_exited = lambda: setattr(
            window, "window_exits", window.window_exits + 1
        )
        window.pane_exited = MainWindow.pane_exited.__get__(window, FakeWindow)
        window._apply_pane_exit = MainWindow._apply_pane_exit.__get__(window, FakeWindow)
        return window

    def run_pending_idle(self):
        """Run the GLib idle callbacks queued by pane_exited."""
        from gi.repository import GLib

        context = GLib.main_context_default()
        # Each pass runs ready idle sources; two passes cover nested scheduling.
        for _ in range(3):
            while context.pending():
                context.iteration(False)

    def test_normal_exit_closes_only_that_pane(self):
        window = self.make_window()
        views = [FakeView(0), FakeView(0), FakeView(0)]
        tab = FakeTab(views)
        window.tabs.append(tab)
        window.pane_exited(tab, views[1])
        self.run_pending_idle()
        self.assertEqual(tab.container.count(), 2)
        self.assertEqual(window.closed_tabs, [])
        self.assertTrue(any("分屏已关闭" in msg for msg in window.statuses))

    def test_abnormal_exit_keeps_the_pane(self):
        window = self.make_window()
        views = [FakeView(0), FakeView(3 << 8)]
        tab = FakeTab(views)
        window.tabs.append(tab)
        window.pane_exited(tab, views[1])
        self.run_pending_idle()
        self.assertEqual(tab.container.count(), 2)
        self.assertTrue(any("异常退出" in msg for msg in window.statuses))

    def test_abnormal_exit_closes_when_configured(self):
        window = self.make_window({"behavior.close_on_abnormal_exit": True})
        views = [FakeView(0), FakeView(3 << 8)]
        tab = FakeTab(views)
        window.tabs.append(tab)
        window.pane_exited(tab, views[1])
        self.run_pending_idle()
        self.assertEqual(tab.container.count(), 1)

    def test_last_pane_closes_the_tab(self):
        window = self.make_window()
        views = [FakeView(0)]
        tab = FakeTab(views)
        window.tabs.append(tab)
        window.pane_exited(tab, views[0])
        self.run_pending_idle()
        self.assertEqual(window.closed_tabs, [(tab, True)])
        self.assertEqual(window.window_exits, 0)

    def test_disabled_setting_keeps_everything(self):
        window = self.make_window({"behavior.close_pane_on_exit": False})
        views = [FakeView(0), FakeView(0)]
        tab = FakeTab(views)
        window.tabs.append(tab)
        window.pane_exited(tab, views[0])
        self.run_pending_idle()
        self.assertEqual(tab.container.count(), 2)
        self.assertEqual(window.closed_tabs, [])

    def test_unknown_view_is_ignored(self):
        window = self.make_window()
        views = [FakeView(0)]
        tab = FakeTab(views)
        window.tabs.append(tab)
        window.pane_exited(tab, FakeView(0))  # not in this tab
        self.run_pending_idle()
        self.assertEqual(tab.container.count(), 1)

    def test_tab_already_gone_is_ignored(self):
        window = self.make_window()
        views = [FakeView(0)]
        tab = FakeTab(views)
        window.pane_exited(tab, views[0])  # tab never added
        self.run_pending_idle()
        self.assertEqual(window.closed_tabs, [])


class ConfigDefaultTests(unittest.TestCase):
    def test_new_defaults_exist(self):
        config = config_mod.Config()
        self.assertIs(config.get("behavior.close_pane_on_exit"), True)
        self.assertIs(config.get("behavior.close_window_on_last_exit"), True)
        self.assertIs(config.get("behavior.close_on_abnormal_exit"), False)

    def test_defaults_can_be_overridden(self):
        config = config_mod.Config(
            {"behavior": {"close_pane_on_exit": "no", "close_on_abnormal_exit": "yes"}}
        )
        self.assertIs(config.get("behavior.close_pane_on_exit"), False)
        self.assertIs(config.get("behavior.close_on_abnormal_exit"), True)

    def test_defaults_round_trip_through_toml(self):
        config = config_mod.Config()
        text = config.to_toml()
        self.assertIn("close_pane_on_exit", text)
        reloaded = config_mod.Config(config_mod.minitoml.loads(text))
        self.assertIs(reloaded.get("behavior.close_pane_on_exit"), True)


if __name__ == "__main__":
    unittest.main()
