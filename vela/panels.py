"""Adapters that let the side panels live inside the split-pane tree.

A pane leaf only requires a widget that understands ``set_focused()``,
``apply_theme()`` and a ``title``.  The terminal provides those directly; the
system monitor and file browser are wrapped here so the pane tree does not need
to know which kind of pane it is holding.

This is also where the pane kinds are named, so the window, the preferences and
the command palette all refer to them the same way.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import theme as theme_mod  # noqa: E402

KIND_TERMINAL = "terminal"
KIND_SYSINFO = "sysinfo"
KIND_FILEBROWSER = "filebrowser"

#: Display names, used in menus and the command palette.
KIND_LABELS: Dict[str, str] = {
    KIND_TERMINAL: "终端",
    KIND_SYSINFO: "系统性能",
    KIND_FILEBROWSER: "文件面板",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def _rgb_floats(color: str) -> tuple:
    """``#rrggbb`` -> floats in 0..1, for cairo."""
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) != 6:
        return (0.5, 0.5, 0.5)
    try:
        return tuple(int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))
    except ValueError:
        return (0.5, 0.5, 0.5)


def is_terminal(kind: str) -> bool:
    return kind == KIND_TERMINAL


class PanelView(Gtk.Box):
    """Wrap a side panel so it can occupy a pane leaf.

    The wrapper forwards the handful of calls the pane tree makes, and owns the
    panel's start/stop lifecycle: the system monitor samples only while it is on
    screen, so hiding a pane has to stop its timer.
    """

    def __init__(
        self,
        kind: str,
        panel: Gtk.Widget,
        theme: theme_mod.Theme,
        on_start: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.kind = kind
        self.panel = panel
        self.theme = theme
        self._on_start = on_start
        self._on_stop = on_stop
        self.focused = False
        self._border_color = theme.accent
        self.get_style_context().add_class("vela-pane")
        self.get_style_context().add_class("vela-panel-pane")
        self.pack_start(panel, True, True, 0)
        # Same focus ring as the terminal panes; a panel that is the active
        # split must look active too.
        self.connect_after("draw", self._on_draw_border)

    # -- pane interface --------------------------------------------------
    @property
    def title(self) -> str:
        return kind_label(self.kind)

    def set_focused(self, focused: bool) -> None:
        """Show or hide the focus ring, matching the terminal panes."""
        if bool(focused) == self.focused:
            return
        self.focused = bool(focused)
        self.queue_draw()

    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        self._border_color = theme.accent
        applier = getattr(self.panel, "apply_theme", None)
        if applier is not None:
            applier(theme)
        self.queue_draw()

    def _on_draw_border(self, _widget, cr) -> bool:
        """Draw the accent ring when this panel pane has the focus."""
        if not self.focused:
            return False
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 2 or height <= 2:
            return False
        red, green, blue = _rgb_floats(self._border_color)
        cr.save()
        cr.set_source_rgba(red, green, blue, 0.9)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, width - 1.0, height - 1.0)
        cr.stroke()
        cr.restore()
        return False

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        """Begin the panel's own work (the monitor's sampling timer).

        The panel is started directly rather than through a window callback:
        ``PaneContainer.split()`` starts the new view before it becomes the
        active pane, so a callback that looked up "the active pane" would start
        the wrong one.
        """
        starter = getattr(self.panel, "start", None)
        if starter is not None:
            starter()
        if self._on_start is not None:
            self._on_start()

    def stop(self) -> None:
        stopper = getattr(self.panel, "stop", None)
        if stopper is not None:
            stopper()
        if self._on_stop is not None:
            self._on_stop()

    def terminate(self) -> None:
        """Called when the pane is closed; panels have no child process."""
        self.stop()

    # -- terminal-only operations are not available on a panel -----------
    @property
    def exited(self) -> bool:
        return False

    @property
    def child_pid(self) -> int:
        return 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PanelView({self.kind})"
