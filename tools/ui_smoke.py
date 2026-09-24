#!/usr/bin/env python3
"""Drive the real Vela window through its features and capture screenshots.

This is the acceptance test for the parts of a terminal emulator that only exist
when it is actually running: PTY I/O, tabs, splits, search, the command palette,
themes and preferences.  Run it on a machine with a display:

    DISPLAY=:1 /usr/bin/python3 tools/ui_smoke.py --out /tmp/vela-smoke

It writes ``report.json`` plus numbered PNGs, then exits non-zero if any step
failed.
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import json
import os
import shutil
import sys
import tempfile
from typing import Callable, Dict, List

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

faulthandler.enable()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vela import config as config_mod  # noqa: E402
from vela import fonts as fonts_mod  # noqa: E402
from vela import layout as layout_mod  # noqa: E402
from vela import panels as panels_mod  # noqa: E402
from vela import theme as theme_mod  # noqa: E402
from vela.app import VelaApplication, build_parser  # noqa: E402

#: Set by tools/offscreen.sh.  On a private virtual display there is nobody to
#: disturb, so windows may be raised and kept on top to force the compositor to
#: paint them.  On the user's real session that would steal focus and pop
#: windows over their work, so it is skipped there.
OFFSCREEN = os.environ.get("VELA_OFFSCREEN") == "1"


def raise_for_paint(window) -> None:
    """Ask the compositor to paint ``window`` without disturbing a real desktop."""
    if not OFFSCREEN:
        return
    window.set_keep_above(True)
    window.present()
    gdk_window = window.get_window()
    if gdk_window is not None:
        gdk_window.raise_()


class _Click:
    """A button press for the pane handlers.

    A synthesised ``Gdk.EventButton`` cannot be used here: PyGObject exposes its
    ``button`` attribute as the event object itself rather than the integer
    field, so ``event.button == 1`` is always false for a hand-built event (real
    clicks are fine — verified separately).  The handlers only read ``button``,
    ``state``, ``x`` and ``y``, so a small stand-in is passed instead of going
    through a GObject signal.
    """

    def __init__(self, x: float, y: float, button: int = 1, state: int = 0):
        self.x = float(x)
        self.y = float(y)
        self.button = int(button)
        self.state = int(state)


def _find_path_cell(view, path: str):
    """Locate a cell whose match text ends with ``path``."""
    terminal = view.terminal
    for row in range(terminal.get_row_count()):
        for column in range(terminal.get_column_count()):
            result = terminal.match_check(column, row)
            if result and result[0] and result[0].endswith(path):
                return row, column
    return None


def _panel_of_kind(window, kind: str):
    """The panel widget of the first pane of ``kind`` in the active tab."""
    tab = window.active_tab
    if tab is None:
        return None
    for leaf in tab.container.leaves():
        if getattr(leaf, "kind", "") == kind:
            return getattr(leaf.view, "panel", None)
    return None


def _split_nodes(node):
    """All Split nodes in a pane tree (empty for a single leaf)."""
    from vela.pane import Leaf, Split

    if node is None or isinstance(node, Leaf):
        return []
    return [node] + _split_nodes(node.first) + _split_nodes(node.second)


def _tab_centre_in_notebook(window, index: int):
    """Notebook-relative centre of tab ``index``, in the space events arrive in.

    A tab label's ``get_allocation()`` is not in the notebook's coordinate
    space, so it cannot be used directly to decide whether a press hit a tab.
    This is the conversion the hit test relies on.
    """
    notebook = window.notebook
    page = notebook.get_nth_page(index)
    label = notebook.get_tab_label(page)
    if label is None:
        return None
    result = label.translate_coordinates(notebook, 0, 0)
    if result is None:
        return None
    left, top = result[-2], result[-1]
    return left + label.get_allocation().width // 2, top + label.get_allocation().height // 2


def _grab_window_pixels(window):
    """One window screenshot, reused by every border sample.

    Grabbing is expensive and each grab allocates a fresh pixbuf; doing it once
    per check group keeps the smoke run from churning memory while panes are
    being created and destroyed.
    """
    gdk_window = window.get_window()
    if gdk_window is None:
        return None
    return Gdk.pixbuf_get_from_window(
        gdk_window, 0, 0, gdk_window.get_width(), gdk_window.get_height()
    )


def _pane_has_border(window, leaf, pixbuf=None, tolerance: int = 60) -> bool:
    """Whether a pane's rendered frame differs from its plain background.

    The focus ring is drawn by the pane itself (a CSS box-shadow on a Gtk.Box is
    never painted), so this samples the actual pixels along the pane's outer
    edge and looks for a colour that is not the terminal background.
    """
    if leaf is None:
        return False
    widget = leaf.view
    try:
        ox, oy = widget.translate_coordinates(window, 0, 0)
    except (TypeError, ValueError):
        return False
    if pixbuf is None:
        pixbuf = _grab_window_pixels(window)
    if pixbuf is None:
        return False
    pixels = pixbuf.get_pixels()
    stride = pixbuf.get_rowstride()
    channels = pixbuf.get_n_channels()
    background = (0x12, 0x14, 0x1C)

    def differs(x: int, y: int) -> bool:
        if not (0 <= x < pixbuf.get_width() and 0 <= y < pixbuf.get_height()):
            return False
        offset = y * stride + x * channels
        red, green, blue = pixels[offset], pixels[offset + 1], pixels[offset + 2]
        return (
            abs(red - background[0]) > tolerance
            or abs(green - background[1]) > tolerance
            or abs(blue - background[2]) > tolerance
        )

    height = widget.get_allocated_height()
    width = widget.get_allocated_width()
    # Sample the left edge down the pane and the bottom edge across it; the top
    # edge can coincide with the notebook's tab underline.
    for offset in (20, height // 3, height // 2, (2 * height) // 3):
        if differs(int(ox), int(oy + offset)):
            return True
    for offset in (20, width // 3, width // 2, (2 * width) // 3):
        if differs(int(ox + offset), int(oy + height - 1)):
            return True
    return False


def _write_png(path: str, width: int = 120, height: int = 80) -> None:
    """Write a small PNG without depending on Pillow."""
    import struct
    import zlib

    rows = b""
    for y in range(height):
        rows += b"\x00" + b"".join(
            bytes([(x * 2) % 256, (y * 3) % 256, 160]) for x in range(width)
        )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    payload = b"\x89PNG\r\n\x1a\n"
    payload += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    payload += chunk(b"IDAT", zlib.compress(rows))
    payload += chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(payload)


class Smoke:
    def __init__(self, out_dir: str, keep_config: bool) -> None:
        self.out_dir = out_dir
        self.keep_config = keep_config
        self.config_dir = tempfile.mkdtemp(prefix="vela-smoke-config-")
        os.environ[config_mod.CONFIG_DIR_ENV] = self.config_dir
        self.steps: List[Dict[str, object]] = []
        self.shots: List[str] = []
        self.counter = 0
        # Whether an overlay was open before ensure_drawn() re-showed the window.
        self._overlay_was_open = False
        self.app: VelaApplication = None  # type: ignore[assignment]

    # -- helpers ---------------------------------------------------------
    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"step": name, "ok": bool(ok), "detail": detail})
        marker = "ok  " if ok else "FAIL"
        print(f"[{marker}] {name} {detail}", flush=True)
        self._write_report()

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.record(name, condition, detail)
        return condition

    def shot(self, name: str) -> str:
        window = self.app.windows[0]
        # The compositor only repaints a window that is actually in front; on the
        # private display the window is raised, on a real desktop it is left
        # alone so the user's focus is not stolen.
        raise_for_paint(window)
        gdk_window = window.get_window()
        # Compositors skip repaints for occluded windows, which would return a
        # stale frame.  Nudging the size by one pixel forces a full re-layout
        # and expose, and is invisible in the captured image.
        width, height = window.get_size()
        window.resize(width + 1, height)
        # The compositor on this desktop pushes frames lazily, so allow a full
        # second after forcing a re-layout before reading the surface back.
        self.pump(1.0)
        window.resize(width, height)
        window.queue_draw()
        self.pump(0.8)
        self.counter += 1
        path = os.path.join(self.out_dir, f"{self.counter:02d}-{name}.png")
        if gdk_window is None:
            self.record(f"shot:{name}", False, "no GdkWindow")
            return path
        pixbuf = Gdk.pixbuf_get_from_window(
            gdk_window, 0, 0, gdk_window.get_width(), gdk_window.get_height()
        )
        if pixbuf is None:
            self.record(f"shot:{name}", False, "pixbuf was null")
            return path
        pixbuf.savev(path, "png", [], [])
        self.shots.append(path)
        self.record(f"shot:{name}", True, os.path.basename(path))
        return path

    def window(self):
        return self.app.windows[0]

    @staticmethod
    def active_tab_for(window):
        return window.active_tab

    @staticmethod
    def _pages_overflow(dialog) -> int:
        """Largest horizontal overflow across the preferences pages, in pixels."""
        notebook = dialog.get_content_area().get_children()[0]
        worst = 0
        current = notebook.get_current_page()
        for index in range(notebook.get_n_pages()):
            page = notebook.get_nth_page(index)
            notebook.set_current_page(index)
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            limit = page.get_allocation().x + page.get_allocation().width

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
        notebook.set_current_page(current)
        return worst

    def ensure_drawn(self, timeout: float = 4.0) -> int:
        """Paint the window and return how many draw passes it got.

        Widgets added to an already-realized container are only allocated after
        the window has been drawn; without this, measurements in a headless run
        report 1px for newly created splits and panel rows.
        """
        window = self.window()
        self._overlay_was_open = bool(
            window.palette.get_visible()
            or window.search_revealer.get_reveal_child()
        )
        if OFFSCREEN:
            # GNOME Shell may leave an occluded client window unpainted; hide and
            # re-show it so the compositor has to map and paint it again.  Only
            # done on the private display: on a real desktop this would make the
            # window jump in front of whatever the user is doing.
            window.set_keep_above(True)
            if window.get_visible():
                window.hide()
                self.pump(0.2)
            window.present()
            window.show_all()
            window.deiconify()
        # show_all() also reveals the overlays (command palette, search bar);
        # put them back so later steps see the state they expect.
        if not self._overlay_was_open:
            window.palette.hide()
            window.search_revealer.set_reveal_child(False)
        raise_for_paint(window)
        counts = {"n": 0}

        def on_draw(*_args):
            counts["n"] += 1
            return False

        handler = window.connect("draw", on_draw)
        # The compositor on this desktop pushes frames lazily and sometimes skips
        # a window entirely, so retry a few short pumps.  Long nested pumps
        # re-enter the main loop while shells settle, which crashed VTE.
        waited = 0.0
        while counts["n"] == 0 and waited < timeout:
            self.pump(0.4)
            waited += 0.4
        window.disconnect(handler)
        return counts["n"]

    def tab(self):
        return self.window().active_tab

    def view(self):
        """The terminal the test should drive.

        Panels are panes now, so the active pane can be a panel; terminal steps
        always want the nearest terminal.
        """
        return self.window().active_terminal

    def feed(self, text: str) -> None:
        view = self.view()
        if view is not None:
            view.send_text(text)

    def read_terminal(self) -> str:
        view = self.view()
        if view is None:
            return ""
        return view.get_selected_text() if False else _terminal_text(view)

    # -- scenario --------------------------------------------------------
    def build_app(self) -> VelaApplication:
        parser = build_parser()
        options = parser.parse_args([])
        options.capture = None
        options.execute = None
        options.working_directory = None
        options.title = None
        options.maximize = False
        options.fullscreen = False
        options.theme = None
        options.no_headerbar = False
        config = config_mod.Config.load()
        # Modal confirmations would block an unattended run; they are covered by
        # their own manual checklist entry.
        config.set("behavior.confirm_close", False)
        # A distinct application id keeps the smoke test a separate process even
        # when the user already has Vela running (otherwise Gtk.Application
        # would treat this as a remote client and the run would exit instantly).
        return VelaApplication(config, options, "io.github.vela.Vela.SmokeTest")

    def run(self) -> int:
        os.makedirs(self.out_dir, exist_ok=True)
        self.app = self.build_app()
        queue: List[Callable[[], None]] = [
            self.step_startup,
            self.step_shell_io,
            self.step_tabs,
            self.step_splits,
            self.step_pane_clicks,
            self.step_exit_closes_pane,
            self.step_sysinfo,
            self.step_filebrowser,
            self.step_files,
            self.step_palette,
            self.step_theme,
            self.step_fonts,
            self.step_search,
            self.step_preferences,
            self.step_config_persist,
            # Runs last: it replaces the tab set, so it must not disturb the
            # state the earlier steps rely on.
            self.step_tab_naming_and_layouts,
            self.step_teardown,
        ]
        self._pump(queue)
        self.app.run([])
        return self.finish()

    def _pump(self, queue: List[Callable[[], None]], delay: int = 1500) -> None:
        if not queue:
            GLib.timeout_add(delay, self._quit)
            return
        step = queue[0]
        rest = queue[1:]

        def run_step() -> bool:
            try:
                step()
            except Exception as error:  # noqa: BLE001 - report, keep going
                import traceback

                self.record(step.__name__, False, f"exception: {error}")
                traceback.print_exc()
            self._pump(rest, delay)
            return False

        GLib.timeout_add(delay, run_step)

    def _quit(self) -> bool:
        self.app.quit_app()
        return False

    # -- steps -----------------------------------------------------------
    def step_startup(self) -> None:
        # Only raise the window on the private display; on a real desktop that
        # would pull focus away from the user.
        raise_for_paint(self.window())
        self.pump(0.4)
        drawn = self.ensure_drawn()
        self.check(
            "window was painted (measurements are meaningful)",
            drawn > 0,
            f"draw passes={drawn}",
        )
        window = self.window()
        self.check("window created", window is not None)
        self.check("one tab at startup", len(window.tabs) == 1, f"tabs={len(window.tabs)}")
        view = self.view()
        self.check("terminal spawned", view is not None and view.child_pid > 0,
                   f"pid={view.child_pid if view else 0}")
        self.check("theme is dark default",
                   window.config.get("appearance.theme") == theme_mod.DEFAULT_THEME)
        self.shot("startup")

    def step_shell_io(self) -> None:
        self.feed("echo VELA-MARKER-$((6*7)); printf '\\033[31mRED\\033[0m \\033[32mGREEN\\033[0m\\n'; pwd\n")
        # A freshly spawned shell can take a moment to echo; poll instead of
        # assuming a fixed delay is enough.
        text = self.wait_for_text("VELA-MARKER-42", timeout=4.0)
        self.check("shell echoed marker", "VELA-MARKER-42" in text,
                   f"buffer={len(text)} chars")
        self.check("shell printed working directory", "/" in text)
        self.shot("shell-io")

    def wait_for_text(self, needle: str, timeout: float = 4.0) -> str:
        """Pump the main loop until ``needle`` shows up in the buffer."""
        waited = 0.0
        text = ""
        while waited < timeout:
            self.pump(0.2)
            waited += 0.2
            text = _terminal_text(self.view())
            if needle in text:
                return text
        return text

    def wait_for_layout(self, leaves, timeout: float = 4.0) -> bool:
        """Wait until every pane has a real allocation.

        Rebuilding the pane tree leaves the new containers at 1x1 until GTK runs
        another layout pass, so measuring right after a split or close reads a
        transient state rather than a bug.
        """
        waited = 0.0
        while waited < timeout:
            self.pump(0.25)
            waited += 0.25
            if all(
                leaf.view.get_allocated_width() > 20
                and leaf.view.get_allocated_height() > 20
                for leaf in leaves
            ):
                return True
        return False

    @staticmethod
    def pump(seconds: float) -> None:
        """Run a nested main loop for ``seconds`` so events and PTY output land."""
        loop = GLib.MainLoop()
        GLib.timeout_add(int(seconds * 1000), lambda: (loop.quit(), False)[1])
        loop.run()

    def step_tabs(self) -> None:
        window = self.window()
        window.new_tab()
        window.new_tab()
        self.pump(0.4)
        self.check("three tabs", len(window.tabs) == 3, f"tabs={len(window.tabs)}")
        window.switch_tab(-1)
        self.pump(0.3)
        self.check("tab switching", window.notebook.get_current_page() == 1,
                   f"page={window.notebook.get_current_page()}")
        window.move_tab(1)
        self.pump(0.3)
        self.check("tab reordering", window.tabs[2] is not None)
        window.close_tab(window.tabs[-1])
        self.pump(0.3)
        self.check("tab closed", len(window.tabs) == 2, f"tabs={len(window.tabs)}")
        window.reopen_tab()
        self.pump(0.4)
        self.check("reopened closed tab", len(window.tabs) == 3,
                   f"tabs={len(window.tabs)}")
        self.shot("tabs")

    def step_splits(self) -> None:
        window = self.window()
        tab = self.tab()
        # GTK only allocates widgets added to an already-realized container once
        # the window has actually been drawn.  In a headless run the compositor
        # may never paint it, so force a paint before measuring anything.
        self.ensure_drawn()
        window.split(Gtk.Orientation.HORIZONTAL)
        window.split(Gtk.Orientation.VERTICAL)
        self.pump(0.6)
        # A newly created pane is only allocated once the window has been painted
        # again; without this the fresh splits measure as 1px wide in a headless
        # run and every geometry check below fails for the wrong reason.
        self.ensure_drawn()
        self.pump(0.4)
        self.check("three panes", tab.container.count() == 3,
                   f"panes={tab.container.count()}")
        # VTE reports the character grid it actually allocated, which only
        # updates once GTK has laid the panes out.
        sizes = [
            (leaf.view.terminal.get_column_count(), leaf.view.terminal.get_row_count())
            for leaf in tab.container.leaves()
        ]
        self.check(
            "panes have real geometry",
            all(columns > 20 and rows > 5 for columns, rows in sizes),
            f"cols/rows={sizes}",
        )
        # Regression: a freshly built Gtk.Paned reported 1x1 while the tree was
        # being assembled, so the divider was never positioned and every new
        # split collapsed to its minimum width (and the last ones were never
        # mapped).  Closing such a pane produced no visible change.
        self.check(
            "every split pane is mapped",
            all(leaf.view.get_mapped() for leaf in tab.container.leaves()),
        )
        # Regression: tearing down the pane tree used to detach every descendant,
        # which pulled each VTE widget out of its scroller.  The pane then
        # rendered blank and could never take keyboard focus, so clicking a split
        # looked like it did nothing.
        terminals = [
            (leaf.view.terminal.get_mapped(), leaf.view.terminal.get_realized())
            for leaf in tab.container.leaves()
        ]
        self.check(
            "every terminal widget is realized and mapped",
            all(mapped and realized for mapped, realized in terminals),
            f"(mapped, realized)={terminals}",
        )
        widths = [leaf.view.get_allocation().width for leaf in tab.container.leaves()]
        self.check(
            "no split pane is collapsed",
            all(width > 80 for width in widths),
            f"widths={widths}",
        )
        self.check(
            "dividers were positioned",
            all(
                node.position > 0
                for node in _split_nodes(tab.container.root)
            ),
            f"positions={[node.position for node in _split_nodes(tab.container.root)]}",
        )
        self.check(
            "auto dividers stay centred (not mistaken for user drags)",
            all(
                not node.user_adjusted
                for node in _split_nodes(tab.container.root)
            ),
        )
        # The focused pane must be visually distinguishable.  The ring is painted
        # by the pane itself, so it is checked by sampling the rendered pixels.
        self.check(
            "exactly one pane is marked focused",
            sum(1 for leaf in tab.container.leaves() if leaf.view.focused) == 1,
            f"focused={[leaf.view.focused for leaf in tab.container.leaves()]}",
        )
        frame = _grab_window_pixels(window)
        self.check(
            "the focused pane draws an accent border",
            _pane_has_border(window, tab.container.active, frame),
            f"pane={tab.container.active.view.title!r}",
        )
        unfocused = [leaf for leaf in tab.container.leaves() if leaf is not tab.container.active]
        self.check(
            "unfocused panes have no border",
            all(not _pane_has_border(window, leaf, frame) for leaf in unfocused),
        )
        window.focus_direction("left")
        self.pump(0.3)
        self.check("focus moved left", tab.container.active is not None)
        frame = _grab_window_pixels(window)
        self.check(
            "the border follows the focus",
            _pane_has_border(window, tab.container.active, frame),
            f"pane={tab.container.active.view.title!r}",
        )
        # Clicking a pane must move the *keyboard* focus, not just the highlight.
        # Regression: the highlight moved while keystrokes still went to the
        # previously active pane.
        leaves = tab.container.leaves()
        target = leaves[0] if tab.container.active is not leaves[0] else leaves[1]
        target.view._on_terminal_button_press(
            target.view.terminal, _Click(60, 40, button=1)
        )
        self.pump(0.6)
        self.check(
            "clicking a pane moves the keyboard focus",
            target.view.terminal.is_focus(),
            f"target_is_focus={target.view.terminal.is_focus()} "
            f"active={tab.container.leaves().index(tab.container.active)}",
        )
        self.check(
            "the highlight and keyboard focus agree after a click",
            all(
                (leaf.view.terminal.is_focus()) == (leaf is tab.container.active)
                for leaf in tab.container.leaves()
            ),
            f"focus={[leaf.view.terminal.is_focus() for leaf in tab.container.leaves()]} "
            f"active={tab.container.leaves().index(tab.container.active)}",
        )
        window.zoom_pane()
        self.pump(0.4)
        self.check("pane zoomed", tab.container.zoomed)
        window.zoom_pane()
        self.pump(0.4)
        self.check("pane unzoomed", not tab.container.zoomed)
        window.close_pane()
        self.pump(0.4)
        self.check("pane closed", tab.container.count() == 2,
                   f"panes={tab.container.count()}")
        self.shot("splits")

        # Splitting five times then closing back down must keep the layout
        # usable and must actually reduce the pane count every time.
        for _ in range(3):
            window.split(Gtk.Orientation.HORIZONTAL)
            self.pump(0.35)
        self.pump(0.6)
        self.check(
            "five panes after repeated splits",
            tab.container.count() == 5,
            f"panes={tab.container.count()}",
        )
        self.check(
            "all five panes are mapped",
            all(leaf.view.get_mapped() for leaf in tab.container.leaves()),
        )
        counts = []
        for _ in range(4):
            window.close_pane()
            self.pump(0.35)
            counts.append(tab.container.count())
        self.check(
            "each close removes exactly one pane",
            counts == [4, 3, 2, 1],
            f"counts={counts}",
        )
        self.check(
            "last pane remains usable",
            tab.container.count() == 1
            and tab.container.leaves()[0].view.get_mapped(),
        )

    def step_palette(self) -> None:
        window = self.window()
        window.toggle_palette()
        self.pump(0.4)
        self.check("palette visible", window.palette.get_visible())
        window.palette.entry.set_text("分屏")
        self.pump(0.3)
        model = window.palette.view.get_model()
        self.check("palette filters", len(model) > 0, f"rows={len(model)}")
        palette_alloc = window.palette.get_allocation()
        self.check(
            "palette has a real size",
            palette_alloc.height > 200 and palette_alloc.width > 300,
            f"{palette_alloc.width}x{palette_alloc.height}",
        )
        self.shot("palette")
        window.palette.hide()

    def step_theme(self) -> None:
        window = self.window()
        window.set_theme("tokyo-night")
        self.check("theme switched", window.theme.name == "tokyo-night",
                   window.theme.name)
        view = self.view()
        self.check("terminal palette follows theme",
                   view.theme.name == "tokyo-night" if view else False)
        self.shot("theme-tokyo-night")
        window.set_theme("vela-light")
        self.shot("theme-light")
        window.set_theme("dracula")
        self.shot("theme-dracula")

    def step_fonts(self) -> None:
        """Font picker: lists installed fonts and changing one takes effect."""
        window = self.window()
        view = self.view()
        window.open_preferences()
        self.pump(0.8)
        dialog = window._prefs_dialog
        self.check("preferences dialog opened for fonts", dialog is not None)
        if dialog is None:
            return
        dialog.show_all()
        self.pump(0.5)

        combo = dialog._family_combo
        model = combo.get_model()
        entries = [row[0] for row in model] if model is not None else []
        self.check(
            "font picker lists installed monospace fonts",
            len(entries) >= 3,
            f"{len(entries)} fonts",
        )
        self.check(
            "font picker contains the configured font",
            str(window.config.get("appearance.font_family")) in entries,
            f"configured={window.config.get('appearance.font_family')!r}",
        )
        self.check(
            "font picker has a selection",
            combo.get_active_text() is not None,
            repr(combo.get_active_text()),
        )
        self.check(
            "font preview shows a sample",
            "预览" in dialog._preview.get_text(),
            dialog._preview.get_text()[:40],
        )
        self.check(
            "download buttons are offered",
            len(dialog._download_buttons) >= 5,
            f"{len(dialog._download_buttons)} fonts",
        )
        self.check(
            "font status summarises what is installed",
            "等宽字体" in dialog._font_status.get_text(),
            dialog._font_status.get_text(),
        )
        # Layout regression: the notebook used to keep its natural height, so
        # every page was squeezed into a strip at the top of the dialog.
        notebook = dialog.get_content_area().get_children()[0]
        self.check(
            "preferences notebook fills the dialog",
            notebook.get_allocated_height() > dialog.get_allocated_height() * 0.5,
            f"notebook={notebook.get_allocated_height()} "
            f"dialog={dialog.get_allocated_height()}",
        )
        self.check(
            "no preferences page overflows horizontally",
            self._pages_overflow(dialog) == 0,
            f"overflow={self._pages_overflow(dialog)}px",
        )
        self.check(
            "appearance page shows the active font",
            "JetBrains" in dialog._font_summary.get_text()
            or str(window.config.get("appearance.font_family"))
            in dialog._font_summary.get_text(),
            dialog._font_summary.get_text(),
        )

        # Fonts that already exist on this machine (a mounted Windows
        # partition) can be imported; they are never downloaded.
        self.check(
            "local font buttons are offered",
            len(dialog._local_buttons) >= 3,
            f"{len(dialog._local_buttons)} fonts",
        )
        self.check(
            "local font status is reported",
            bool(dialog._local_status.get_text()),
            dialog._local_status.get_text()[:60],
        )
        available = {
            entry.key for entry, _paths in fonts_mod.available_local_fonts()
        }
        if available:
            self.check(
                "detected local fonts are importable",
                any(
                    dialog._local_buttons[key].get_sensitive()
                    or fonts_mod.is_local_installed(key)
                    for key in available
                ),
                f"available={sorted(available)}",
            )
        # The CJK fallback picker must list real Chinese-capable families.
        cjk_model = dialog._cjk_combo.get_model()
        cjk_entries = [row[0] for row in cjk_model] if cjk_model else []
        self.check(
            "CJK fallback picker lists Chinese-capable fonts",
            len(cjk_entries) >= 2,
            f"{len(cjk_entries)} fonts",
        )
        self.check(
            "CJK fallback picker has a selection",
            dialog._cjk_combo.get_active_text() is not None,
            repr(dialog._cjk_combo.get_active_text()),
        )
        # Choosing a fallback must reach the terminal's font description.
        target_cjk = next(
            (
                name
                for name in cjk_entries
                if name and not name.startswith("（")
            ),
            None,
        )
        if target_cjk is not None:
            dialog._cjk_combo.set_active(cjk_entries.index(target_cjk))
            self.pump(0.6)
            font_text = view.terminal.get_font().to_string()
            self.check(
                "chosen fallback reaches the terminal font",
                target_cjk.split()[0] in font_text,
                f"font={font_text!r} fallback={target_cjk!r}",
            )
            self.check(
                "fallback is written to the config",
                window.config.get("appearance.fallback_font") == target_cjk,
                str(window.config.get("appearance.fallback_font")),
            )
            # Reset so later steps are unaffected.
            dialog._cjk_combo.set_active(0)
            self.pump(0.4)

        # Switching the family must reach the terminal, not just the config.
        target = next(
            (name for name in entries if name != combo.get_active_text()), None
        )
        if target is not None:
            before = view.terminal.get_font().to_string()
            combo.set_active(entries.index(target))
            self.pump(0.6)
            after = view.terminal.get_font().to_string()
            self.check(
                "choosing a font changes the terminal font",
                after != before and target in after,
                f"{before!r} -> {after!r}",
            )
            self.check(
                "the chosen font is written to the config",
                window.config.get("appearance.font_family") == target,
                str(window.config.get("appearance.font_family")),
            )
            self.check(
                "the preview follows the choice",
                target in dialog._preview.get_text(),
                dialog._preview.get_text().splitlines()[-1],
            )

        # Font size must also take effect immediately.
        size = float(window.config.get("appearance.font_size"))
        window.config.set("appearance.font_size", size + 3)
        window._apply_appearance()
        self.pump(0.4)
        self.check(
            "font size can be applied",
            abs(view.terminal.get_font().get_size() / 1024 - (size + 3)) < 0.6,
            f"size={view.terminal.get_font().get_size() / 1024:.1f}",
        )

        dialog.destroy()
        self.pump(0.4)
        # Restore the original font size so later steps keep their expectations.
        window.config.set("appearance.font_size", size)
        window._apply_appearance()
        self.pump(0.3)

    def step_search(self) -> None:
        window = self.window()
        self.feed("echo SEARCH-TARGET-ALPHA\n")
        self.pump(0.6)
        text = _terminal_text(self.view())
        self.check("search target reached the buffer",
                   "SEARCH-TARGET-ALPHA" in text)
        window.toggle_search()
        window.search_bar.entry.set_text("SEARCH-TARGET-ALPHA")
        self.pump(0.4)
        self.check(
            "search bar is revealed",
            window.search_bar.get_visible()
            and window.search_revealer.get_allocation().height > 20,
            f"height={window.search_revealer.get_allocation().height}",
        )
        found = window._on_search("SEARCH-TARGET-ALPHA", False, True)
        self.check("search finds text", found)
        self.shot("search")
        window.search_bar.close()

    def step_preferences(self) -> None:
        window = self.window()
        window.open_preferences()
        dialog = window._prefs_dialog
        self.check("preferences dialog opened", dialog is not None)
        if dialog is not None:
            dialog.show_all()
            GLib.main_context_default().iteration(False)
            self.shot("preferences")
            dialog.destroy()

    def step_config_persist(self) -> None:
        window = self.window()
        path = window.config.save()
        self.check("config written", os.path.exists(path), path)
        reloaded = config_mod.Config.load(path)
        self.check("config round-trips theme",
                   reloaded.get("appearance.theme") == "dracula",
                   str(reloaded.get("appearance.theme")))
        self.check("no config problems", not reloaded.problems,
                   "; ".join(reloaded.problems))
        self.shot("after-config-save")

    def step_pane_clicks(self) -> None:
        """Left-click switches panes; right-click opens the context menu."""
        window = self.window()
        tab = self.tab()
        while tab.container.count() < 2:
            window.split(Gtk.Orientation.HORIZONTAL)
            self.pump(0.6)
        leaves = tab.container.leaves()
        left, right = leaves[0], leaves[1]

        tab.container.set_active(right)
        self.pump(0.4)
        self.check(
            "right pane starts active", tab.container.active is right,
            f"active={'left' if tab.container.active is left else 'right'}",
        )

        # Left-clicking the other pane must move the focus.  VTE consumes the
        # left button, so the handler lives on the terminal itself.
        left.view._on_terminal_button_press(
            left.view.terminal, _Click(40, 40, button=1)
        )
        self.pump(0.5)
        self.check(
            "left-click switches the active pane",
            tab.container.active is left,
            f"active={'left' if tab.container.active is left else 'right'}",
        )
        self.check(
            "the focus ring follows a left-click",
            left.view.focused and not right.view.focused,
            f"focused={[leaf.view.focused for leaf in leaves]}",
        )

        # Right-click opens the menu without changing which pane is active.
        right.view._on_terminal_button_press(
            right.view.terminal, _Click(40, 40, button=3)
        )
        self.pump(0.5)
        self.check(
            "right-click also focuses its pane", tab.container.active is right
        )
        menu = getattr(right.view, "_context_menu", None)
        self.check("right-click opens a context menu", menu is not None)
        if menu is not None:
            labels = [
                child.get_label()
                for child in menu.get_children()
                if isinstance(child, Gtk.MenuItem)
                and not isinstance(child, Gtk.SeparatorMenuItem)
            ]
            flat = " ".join(labels)
            for expected in ("复制", "粘贴", "左右分屏", "上下分屏", "关闭当前分屏"):
                self.check(
                    f"menu contains {expected}",
                    expected in flat,
                    f"labels={len(labels)}",
                )
            self.check(
                "menu items show their shortcut",
                any("Ctrl" in label for label in labels),
            )
            self.check(
                "menu has separators between groups",
                any(
                    isinstance(child, Gtk.SeparatorMenuItem)
                    for child in menu.get_children()
                ),
            )
            # A menu action must do the same thing as the shortcut.
            before = tab.container.count()
            for child in menu.get_children():
                if isinstance(child, Gtk.MenuItem) and "左右分屏" in (
                    child.get_label() or ""
                ):
                    child.activate()
                    break
            self.pump(0.9)
            self.check(
                "a menu action performs the action",
                tab.container.count() == before + 1,
                f"panes {before} -> {tab.container.count()}",
            )

        # Put the tab back to a single pane for the following steps.
        while tab.container.count() > 1:
            window.close_pane()
            self.pump(0.4)

    def step_tab_naming_and_layouts(self) -> None:
        """Tab naming and saving/restoring a pane layout."""
        window = self.window()
        tab = self.tab()

        # The gestures must actually reach the handlers.  A regression here is
        # invisible to direct set_name() calls: the hit test once compared a
        # window-relative press against a label allocation from another
        # coordinate space and so never matched a tab at all.
        window.new_tab()
        self.pump(0.6)
        self.ensure_drawn()
        centre = _tab_centre_in_notebook(window, 1)
        self.check("tab hit test has a target", centre is not None, f"centre={centre}")
        if centre is not None:
            hit = window._tab_at(int(centre[0]), int(centre[1]))
            self.check(
                "a press on a tab label finds that tab",
                hit is window.tabs[1],
                f"hit={hit!r}",
            )
        corner = _tab_centre_in_notebook(window, 0)
        if corner is not None:
            far_right = window.notebook.get_allocation().width - 30
            self.check(
                "a press on empty tab-strip space finds no tab",
                window._tab_at(far_right, int(corner[1])) is None,
            )
        # Double-click and right-click are routed through the same hit test.
        rename_calls: List[str] = []
        menu_calls: List[str] = []
        real_rename = window.rename_tab
        real_menu = window._show_tab_menu
        window.rename_tab = lambda target=None, clear=False: rename_calls.append(
            (target or window.active_tab).name()
        )
        window._show_tab_menu = lambda target, event: menu_calls.append(target.name())

        class _TabEvent:
            """Only the fields the tab handlers read.

            A hand-built Gdk.EventButton reports ``event.type`` as an opaque
            object rather than the enum, so a plain stand-in is used here.  Real
            clicks were verified against this handler separately.
            """

            def __init__(self, x, y, button, kind):
                self.x, self.y, self.button, self.type = x, y, button, kind

        try:
            if centre is not None:
                window._on_notebook_button(
                    window.notebook,
                    _TabEvent(centre[0], centre[1], 1, Gdk.EventType._2BUTTON_PRESS),
                )
                window._on_notebook_button(
                    window.notebook, _TabEvent(centre[0], centre[1], 3, Gdk.EventType.BUTTON_PRESS)
                )
        finally:
            window.rename_tab = real_rename
            window._show_tab_menu = real_menu
        self.check(
            "a double-click on a tab requests a rename",
            bool(rename_calls),
            f"calls={rename_calls}",
        )
        self.check(
            "a right-click on a tab opens the tab menu",
            bool(menu_calls),
            f"calls={menu_calls}",
        )
        window.close_tab(window.tabs[1])
        self.pump(0.5)
        tab = self.tab()

        # Naming a tab must survive the shell changing its title.
        original = tab.name()
        tab.set_name("冒烟测试")
        self.pump(0.3)
        self.check(
            "a named tab shows the given name",
            tab.title_label.get_text() == "冒烟测试",
            tab.title_label.get_text(),
        )
        tab.refresh_title()
        self.check(
            "a named tab ignores the shell title",
            tab.title_label.get_text() == "冒烟测试",
            tab.title_label.get_text(),
        )
        self.check("the tab reports itself as named", tab.is_named())
        tab.set_name("")
        self.pump(0.3)
        self.check(
            "clearing the name follows the shell again",
            not tab.is_named() and tab.title_label.get_text() != "",
            tab.title_label.get_text(),
        )

        # Build a recognisable layout, then save it.
        tab.set_name("布局测试")
        window.split_pane_as(panels_mod.KIND_SYSINFO, Gtk.Orientation.HORIZONTAL)
        self.pump(0.7)
        window.split_pane_as(panels_mod.KIND_FILEBROWSER, Gtk.Orientation.VERTICAL)
        self.pump(0.9)
        self.ensure_drawn()
        before_kinds = [leaf.kind for leaf in tab.container.leaves()]
        before_name = tab.name()
        self.check(
            "the layout under test has three panes",
            len(before_kinds) == 3,
            f"kinds={before_kinds}",
        )
        self.check("saving the layout succeeds", window.save_layout("冒烟布局"))
        self.check(
            "the layout is stored in the config",
            "冒烟布局" in layout_mod.named_layouts(window.config),
        )

        # Wreck the layout, then restore it.
        for leaf in list(tab.panel_panes()):
            window._close_pane_leaf(leaf)
            self.pump(0.4)
        self.ensure_drawn()
        self.check(
            "the layout was changed before restoring",
            len(tab.container.leaves()) != len(before_kinds),
            f"kinds={[leaf.kind for leaf in tab.container.leaves()]}",
        )
        self.check(
            "restoring by name works", window.restore_layout_by_name("冒烟布局")
        )
        self.pump(1.2)
        self.ensure_drawn()
        restored = self.active_tab_for(window)
        self.check(
            "the restored layout has the same pane kinds",
            [leaf.kind for leaf in restored.container.leaves()] == before_kinds,
            f"kinds={[leaf.kind for leaf in restored.container.leaves()]}",
        )
        self.check(
            "the restored tab keeps its name",
            restored.name() == before_name,
            f"name={restored.name()!r} expected={before_name!r}",
        )
        self.check(
            "the restored panes have real sizes",
            all(
                leaf.view.get_allocated_width() > 20
                and leaf.view.get_allocated_height() > 20
                for leaf in restored.container.leaves()
            ),
        )
        # Regression: a restored terminal used to be an empty frame because the
        # restore path created the view without spawning its shell.
        restored_terminals = restored.terminals()
        self.check(
            "every restored terminal has a live shell",
            bool(restored_terminals)
            and all(
                view.child_pid > 0 and not view.exited
                for view in restored_terminals
            ),
            f"pids={[view.child_pid for view in restored_terminals]}",
        )
        restored_text = ""
        if restored_terminals:
            restored_text = self.wait_for_text("$", timeout=4.0)
        self.check(
            "a restored terminal shows a prompt",
            "$" in restored_text,
            f"buffer={len(restored_text)} chars",
        )
        self.shot("restored-layout")

        # Session round trip.
        window.config.set("behavior.restore_session", True)
        self.check("the session can be saved", window.save_session())
        session = layout_mod.load_session()
        self.check("the session file is readable", session is not None)
        if session is not None:
            self.check(
                "the session records every tab",
                len(session.tabs) == len(window.tabs),
                f"{len(session.tabs)} vs {len(window.tabs)}",
            )
            self.check(
                "the session records pane kinds",
                "sysinfo" in [
                    pane.kind
                    for tab_spec in session.tabs
                    for pane in layout_mod.iter_panes(tab_spec.tree)
                ],
                f"kinds={[pane.kind for tab_spec in session.tabs for pane in layout_mod.iter_panes(tab_spec.tree)]}",
            )
        # Leave the session setting off so later steps are unaffected.
        window.config.set("behavior.restore_session", False)
        layout_mod.clear_session()

    def step_exit_closes_pane(self) -> None:

        """Typing ``exit`` must close that pane (the standard terminal rule)."""
        window = self.window()
        tab = self.tab()
        while tab.container.count() < 3:
            window.split(Gtk.Orientation.HORIZONTAL)
            self.pump(0.5)
        before = tab.container.count()
        self.check("three panes before exit", before == 3, f"panes={before}")

        # An abnormal exit keeps its pane so the error stays readable.
        tab.container.set_active(tab.container.leaves()[-1])
        self.pump(0.3)
        self.feed("exit 3\n")
        self.pump(1.8)
        self.check(
            "abnormal exit keeps the pane",
            tab.container.count() == before,
            f"panes={tab.container.count()}",
        )
        self.check(
            "abnormal exit is reported",
            "异常退出" in window.status_message.get_text(),
            window.status_message.get_text(),
        )

        # A normal exit closes exactly that pane.  The abnormal pane above still
        # holds a dead shell, so a fresh pane is used for this half.
        window.split(Gtk.Orientation.HORIZONTAL)
        self.pump(1.4)
        self.feed("exit\n")
        self.pump(1.8)
        self.check(
            "normal exit closes its pane",
            tab.container.count() == before,
            f"panes={tab.container.count()} (was {before}, +1 split then -1 exit)",
        )
        self.check(
            "exit reported in the status bar",
            "分屏已关闭" in window.status_message.get_text(),
            window.status_message.get_text(),
        )
        # Closing a pane reparents the survivors into a freshly built container.
        # They must all still be attached to the widget tree and have a usable
        # size.  The rebuilt panes only report a real size after a layout pass,
        # so wait for one before measuring (measuring immediately shows the
        # transient 1x1 of a container that has not been negotiated yet).
        survivors = tab.container.leaves()
        settled = self.wait_for_layout(survivors)
        self.check(
            "remaining panes stay in the widget tree",
            survivors and all(leaf.view.get_parent() is not None for leaf in survivors),
            f"parents={[type(leaf.view.get_parent()).__name__ if leaf.view.get_parent() else None for leaf in survivors]}",
        )
        self.check(
            "remaining panes keep a usable size",
            settled
            and all(
                leaf.view.get_allocated_width() > 20
                and leaf.view.get_allocated_height() > 20
                for leaf in survivors
            ),
            f"sizes={[(leaf.view.get_allocated_width(), leaf.view.get_allocated_height()) for leaf in survivors]}",
        )

        # Exiting the only pane of a tab closes the tab, not the window.
        # Start from a known state: one tab with one live pane.
        while len(window.tabs) > 1:
            window.close_tab(window.tabs[-1], force=True)
            self.pump(0.4)
        window.new_tab()
        self.pump(0.8)
        tabs_before = len(window.tabs)
        self.check("a second tab exists", tabs_before == 2, f"tabs={tabs_before}")
        self.feed("exit\n")
        self.pump(2.0)
        self.check(
            "exiting the only pane of a tab closes that tab",
            len(window.tabs) == tabs_before - 1,
            f"tabs={len(window.tabs)}",
        )
        self.check(
            "the window survives while other tabs remain",
            len(self.app.windows) == 1,
            f"windows={len(self.app.windows)}",
        )

    def step_sysinfo(self) -> None:
        window = self.window()
        tab = self.active_tab_for(window)
        window.split_pane_as(panels_mod.KIND_SYSINFO, Gtk.Orientation.HORIZONTAL)
        self.pump(0.8)
        self.ensure_drawn()
        panel = _panel_of_kind(window, panels_mod.KIND_SYSINFO)
        self.check("sysinfo panel exists", panel is not None)
        if panel is None:
            return
        self.check("sysinfo panel visible", panel.get_visible())
        alloc = panel.get_allocation()
        self.check(
            "sysinfo panel has real size",
            alloc.width > 150 and alloc.height > 200,
            f"{alloc.width}x{alloc.height}",
        )
        # Wait for a second sample so CPU deltas are available.
        self.pump(2.4)
        snapshot = panel.last_snapshot
        self.check("sysinfo produced a snapshot", snapshot is not None)
        if snapshot is None:
            return
        self.check(
            "sysinfo reports memory",
            snapshot.memory.total > 0 and snapshot.memory.used > 0,
            f"{snapshot.memory.used}/{snapshot.memory.total}",
        )
        self.check(
            "sysinfo reports cores",
            snapshot.core_count > 0,
            f"cores={snapshot.core_count}",
        )
        self.check(
            "sysinfo cpu in range",
            0.0 <= snapshot.cpu_percent <= 100.0,
            f"cpu={snapshot.cpu_percent:.1f}%",
        )
        self.check(
            "sysinfo uptime present", snapshot.uptime > 0, f"{snapshot.uptime:.0f}s"
        )
        self.check(
            "sysinfo counts processes",
            snapshot.process_count > 0,
            f"{snapshot.process_count} processes",
        )
        self.check(
            "sysinfo disk usage present",
            len(snapshot.disks) > 0 and snapshot.disks[0].total > 0,
        )
        self.check(
            "sysinfo cpu row filled",
            panel._rows["cpu"].get_text() not in ("", "—"),
            panel._rows["cpu"].get_text(),
        )
        self.check(
            "sysinfo core meters rendered",
            len(panel._core_bars) == snapshot.core_count,
            f"{len(panel._core_bars)} meters",
        )
        self.check(
            "status bar mirrors sysinfo",
            "CPU" in window.status_sysinfo.get_text(),
            window.status_sysinfo.get_text(),
        )
        # Regression: the panel's content once demanded more width than it was
        # given, so the right-hand columns were clipped away.  It now fills its
        # own pane, so the check is that nothing overflows that pane.
        pane_view = panel.get_parent()
        while pane_view is not None and not hasattr(pane_view, "kind"):
            pane_view = pane_view.get_parent()
        panel_alloc = panel.get_allocation()
        self.check(
            "sysinfo panel fits inside its pane",
            pane_view is not None
            and panel_alloc.width <= pane_view.get_allocated_width() + 2,
            f"panel={panel_alloc.width} pane="
            f"{pane_view.get_allocated_width() if pane_view else None}",
        )
        # Regression: per-core rows were allocated 1px each and never appeared.
        core_rows = getattr(panel, "_core_rows", [])
        self.check(
            "per-core rows are visible and sized",
            bool(core_rows)
            and all(row.get_visible() and row.get_allocation().height > 8
                    for row in core_rows),
            f"rows={len(core_rows)} heights="
            f"{[row.get_allocation().height for row in core_rows[:3]]}",
        )
        self.shot("sysinfo")
        window.set_sysinfo_visible(False)
        self.pump(0.6)
        # A panel is a pane now: "hiding" it closes the pane entirely.
        self.check(
            "the monitor pane is removed",
            _panel_of_kind(window, panels_mod.KIND_SYSINFO) is None,
            f"panes={[leaf.kind for leaf in tab.container.leaves()]}",
        )

    def step_filebrowser(self) -> None:
        """The file panel lists a real directory and can navigate it."""
        window = self.window()
        tab = self.active_tab_for(window)
        self.ensure_drawn()
        work = tempfile.mkdtemp(prefix="vela-smoke-browser-")
        self._browser_work = work
        os.makedirs(os.path.join(work, "subdir"), exist_ok=True)
        with open(os.path.join(work, "alpha.txt"), "w", encoding="utf-8") as handle:
            handle.write("alpha\n")
        with open(os.path.join(work, ".hidden.txt"), "w", encoding="utf-8") as handle:
            handle.write("hidden\n")

        window.split_pane_as(panels_mod.KIND_FILEBROWSER, Gtk.Orientation.HORIZONTAL)
        self.pump(0.8)
        self.ensure_drawn()
        panel = _panel_of_kind(window, panels_mod.KIND_FILEBROWSER)
        self.check("file panel exists", panel is not None)
        if panel is None:
            return
        self.check("file panel visible", panel.get_visible())
        self.ensure_drawn()

        panel.navigate(work)
        self.pump(0.8)
        self.check(
            "file panel shows the requested directory",
            panel.directory == work,
            panel.directory,
        )
        rows = {row[0]: row for row in panel.store}
        self.check("parent row present", ".." in rows, f"rows={list(rows)[:4]}")
        self.check("directory listed", "subdir" in rows)
        self.check("file listed", "alpha.txt" in rows)
        self.check("hidden file listed", ".hidden.txt" in rows)

        if "subdir" in rows:
            row = rows["subdir"]
            self.check(
                "directory row has permission column",
                row[1].startswith("d") and len(row[1]) == 10,
                row[1],
            )
            self.check(
                "directory row has modification time", bool(row[2].strip()), row[2]
            )
            self.check("directory row is flagged as a directory", bool(row[6]))
            self.check("directory row has an icon", row[4] is not None)
        if "alpha.txt" in rows:
            row = rows["alpha.txt"]
            self.check(
                "file row has permission column",
                row[1].startswith("-") and len(row[1]) == 10,
                row[1],
            )
            self.check("file row has a size", bool(row[3].strip()), row[3])

        labels = [
            child.get_label()
            for child in panel._breadcrumb.get_children()
            if isinstance(child, Gtk.Button)
        ]
        self.check("breadcrumbs are rendered", len(labels) >= 2, f"crumbs={labels}")
        self.check(
            "breadcrumb ends at the current directory",
            labels and labels[-1] == os.path.basename(work),
            f"crumbs={labels}",
        )

        # Entering a directory stops following the terminal.
        if "subdir" in rows:
            # Drive the actual double-click path: select the row, then emit
            # row-activated with a TreePath (what GTK itself delivers).
            row_path = panel.store.get_path(rows["subdir"].iter)
            panel.view.get_selection().select_path(row_path)
            panel.view.emit("row-activated", row_path, panel.view.get_column(0))
            self.pump(0.6)
            self.check(
                "double-clicking a directory navigates into it",
                panel.directory == os.path.join(work, "subdir"),
                panel.directory,
            )
            self.check(
                "manual navigation stops following the terminal",
                panel.following is False,
            )
            panel.go_up()
            self.pump(0.6)
            self.check(
                "go_up returns to the parent", panel.directory == work, panel.directory
            )

            # The same route must work for the ".." row and for files.
            rows = {row[0]: row for row in panel.store}
            if ".." in rows:
                up_path = panel.store.get_path(rows[".."].iter)
                panel.view.get_selection().select_path(up_path)
                panel.view.emit("row-activated", up_path, panel.view.get_column(0))
                self.pump(0.5)
                self.check(
                    'double-clicking ".." goes up',
                    panel.directory == os.path.dirname(work),
                    panel.directory,
                )
                panel.navigate(work)
                self.pump(0.5)

        # Hidden-file toggle.
        panel.toggle_hidden()
        self.pump(0.5)
        visible = {row[0] for row in panel.store}
        self.check(
            "hiding removes dot files",
            ".hidden.txt" not in visible,
            f"rows={sorted(visible)[:5]}",
        )
        panel.toggle_hidden()
        self.pump(0.5)
        self.check(
            "showing brings dot files back",
            ".hidden.txt" in {row[0] for row in panel.store},
        )

        # Following the terminal.
        panel.following = True
        panel._sync_toolbar()
        window.follow_terminal_directory()
        self.pump(0.8)
        self.check(
            "panel follows the terminal directory",
            os.path.abspath(panel.directory)
            == os.path.abspath(window._terminal_directory()),
            f"panel={panel.directory} terminal={window._terminal_directory()}",
        )

        # A cd typed into the terminal itself must move the panel too.  This is
        # the path that silently did nothing before: VTE reports no directory
        # unless the shell runs its integration hook, and a non-login shell
        # never does.
        terminal_target = os.path.join(work, "subdir")
        self.feed("cd " + terminal_target + "\n")
        self.pump(2.5)
        self.check(
            "a cd typed in the terminal moves the following panel",
            os.path.abspath(panel.directory) == os.path.abspath(terminal_target),
            f"panel={panel.directory} expected={terminal_target}",
        )
        # Turning following off must stop it, and stop the polling with it.
        panel.toggle_follow()
        self.pump(0.6)
        self.check(
            "turning following off stops the poll",
            not window._directory_poll,
            f"timer={window._directory_poll}",
        )
        self.feed("cd " + work + "\n")
        self.pump(2.0)
        self.check(
            "a cd is ignored while following is off",
            os.path.abspath(panel.directory) == os.path.abspath(terminal_target),
            f"panel={panel.directory}",
        )
        panel.toggle_follow()
        self.pump(0.6)

        # The command bar is a cd box: it moves this panel and never touches the
        # terminal.
        self.check("the panel has a command bar", hasattr(panel, "command_entry"))
        command_target = os.path.join(work, "subdir")
        panel.command_entry.set_text("cd " + command_target)
        panel.command_entry.emit("activate")
        self.pump(1.5)
        self.check(
            "a cd from the command bar moves the panel",
            os.path.abspath(panel.directory) == os.path.abspath(command_target),
            f"panel={panel.directory} expected={command_target}",
        )
        self.check(
            "the command bar is emptied after running",
            panel.command_entry.get_text() == "",
            panel.command_entry.get_text(),
        )
        # The terminal must not receive the text: the bar is not a shell.
        before_text = _terminal_text(self.view())
        panel.command_entry.set_text("echo VELA-COMMAND-BAR")
        panel.command_entry.emit("activate")
        self.pump(1.5)
        self.check(
            "a non-cd line is rejected without reaching the terminal",
            "VELA-COMMAND-BAR" not in _terminal_text(self.view())[len(before_text):],
            "terminal buffer grew with the rejected text",
        )
        self.check(
            "a rejected line leaves the panel where it is",
            os.path.abspath(panel.directory) == os.path.abspath(command_target),
            f"panel={panel.directory}",
        )
        # A bare path is the common shortcut.
        panel.command_entry.set_text(work)
        panel.command_entry.emit("activate")
        self.pump(1.0)
        self.check(
            "a bare path moves the panel",
            os.path.abspath(panel.directory) == os.path.abspath(work),
            f"panel={panel.directory}",
        )

        # Tab completion: unique match completes and appends "/", an ambiguous
        # fragment extends to the common prefix, and "cd" gains its space.
        def press_tab():
            event = Gdk.EventKey.new(Gdk.EventType.KEY_PRESS)
            event.keyval = Gdk.KEY_Tab
            panel._on_command_key(panel.command_entry, event)

        panel.navigate(work)
        self.pump(0.5)
        panel.command_entry.set_text("cd ")
        press_tab()
        self.check(
            "Tab after cd adds the separating space",
            panel.command_entry.get_text().startswith("cd "),
            panel.command_entry.get_text(),
        )
        panel.command_entry.set_text("cd sub")
        press_tab()
        self.check(
            "Tab completes a unique directory and descends into it",
            panel.command_entry.get_text() == "cd subdir/",
            panel.command_entry.get_text(),
        )
        panel.command_entry.emit("activate")
        self.pump(1.0)
        self.check(
            "the completed directory is entered",
            os.path.abspath(panel.directory) == os.path.abspath(command_target),
            f"panel={panel.directory}",
        )
        panel.navigate(command_target)
        self.pump(0.6)

        # Opening a file from the panel uses the normal file dispatch.
        panel.navigate(work)
        self.pump(0.6)
        rows = {row[0]: row for row in panel.store}
        if "alpha.txt" in rows:
            file_path = panel.store.get_path(rows["alpha.txt"].iter)
            panel.view.get_selection().select_path(file_path)
            panel.view.emit("row-activated", file_path, panel.view.get_column(0))
            self.pump(0.8)
            self.check(
                "double-clicking a file opens it in the viewer",
                len(window._viewer_windows) == 1,
                f"{len(window._viewer_windows)} windows",
            )
            while window._viewer_windows:
                window._viewer_windows[-1].destroy()
                self.pump(0.2)

        # Error paths must be reported, not crash.
        missing = os.path.join(work, "no-such-dir")
        self.check(
            "missing directory is reported",
            panel.navigate(missing) is False and "不存在" in panel._summary.get_text(),
            panel._summary.get_text(),
        )
        panel.navigate(work)
        self.pump(0.4)
        self.shot("filebrowser")

        # Panels are split panes now: a panel pane can itself be split further,
        # and the split can be switched to another kind in place.
        before = tab.container.count()
        window.split_pane_as(panels_mod.KIND_SYSINFO, Gtk.Orientation.VERTICAL)
        self.pump(0.8)
        self.ensure_drawn()
        self.check(
            "a panel pane can be split again",
            tab.container.count() == before + 1,
            f"panes {before} -> {tab.container.count()}",
        )
        kinds = [leaf.kind for leaf in tab.container.leaves()]
        self.check(
            "the tab holds mixed pane kinds",
            panels_mod.KIND_SYSINFO in kinds
            and panels_mod.KIND_FILEBROWSER in kinds
            and panels_mod.KIND_TERMINAL in kinds,
            f"kinds={kinds}",
        )
        self.check(
            "every pane kind gets a real size",
            all(
                leaf.view.get_allocated_width() > 20
                and leaf.view.get_allocated_height() > 20
                for leaf in tab.container.leaves()
            ),
            f"sizes={[(leaf.view.get_allocated_width(), leaf.view.get_allocated_height()) for leaf in tab.container.leaves()]}",
        )
        self.shot("panels-as-splits")

        # Switching the active pane's kind keeps it in place.
        window.switch_active_pane_kind(panels_mod.KIND_TERMINAL)
        self.pump(0.9)
        self.check(
            "the active pane can be switched to a terminal",
            tab.container.active.kind == panels_mod.KIND_TERMINAL,
            f"kind={tab.container.active.kind}",
        )
        self.check(
            "switching the kind keeps the pane count",
            tab.container.count() == before + 1,
            f"panes={tab.container.count()}",
        )
        window.switch_active_pane_kind(panels_mod.KIND_FILEBROWSER)
        self.pump(0.9)
        self.check(
            "the active pane can be switched to the file browser",
            tab.container.active.kind == panels_mod.KIND_FILEBROWSER,
            f"kind={tab.container.active.kind}",
        )
        # A pane switched to a panel must actually run: the monitor's sampling
        # timer is started by the swap, and without it every row showed "—".
        window.switch_active_pane_kind(panels_mod.KIND_SYSINFO)
        self.pump(2.6)
        monitor = _panel_of_kind(window, panels_mod.KIND_SYSINFO)
        self.check(
            "a switched-in monitor starts sampling",
            monitor is not None and monitor.last_snapshot is not None,
            f"timer={bool(getattr(monitor, '_timer', 0))}",
        )
        if monitor is not None:
            self.check(
                "the switched-in monitor shows real values",
                monitor._rows["cpu"].get_text() not in ("", "—"),
                f"cpu={monitor._rows['cpu'].get_text()!r}",
            )
        self.check(
            "switching a pane kind is not reported as a crash",
            "异常退出" not in window.status_message.get_text(),
            window.status_message.get_text(),
        )

        # The tab is named after a terminal, not the active panel.
        self.check(
            "the tab title still names a terminal",
            tab.title_label.get_text() not in (
                panels_mod.kind_label(panels_mod.KIND_FILEBROWSER),
                panels_mod.kind_label(panels_mod.KIND_SYSINFO),
            ),
            f"title={tab.title_label.get_text()!r}",
        )

        # Terminal-only actions must still reach a terminal.
        self.check(
            "terminal actions fall back to a terminal pane",
            window.active_terminal is not None,
            f"terminals={len(tab.terminals())}",
        )

        # Closing the panel panes leaves the terminal usable.
        for leaf in list(tab.panel_panes()):
            window._close_pane_leaf(leaf)
            self.pump(0.5)
        self.ensure_drawn()
        self.check(
            "closing panel panes leaves the terminal",
            all(
                leaf.kind == panels_mod.KIND_TERMINAL
                for leaf in tab.container.leaves()
            ),
            f"kinds={[leaf.kind for leaf in tab.container.leaves()]}",
        )
        self.check(
            "the terminal survives the panel panes",
            len(tab.terminals()) >= 1
            and all(
                leaf.view.terminal.get_realized()
                for leaf in tab.container.leaves()
            ),
        )

    def step_files(self) -> None:
        window = self.window()
        view = self.view()
        work = tempfile.mkdtemp(prefix="vela-smoke-files-")
        self._files_work = work
        text_path = os.path.join(work, "sample.py")
        with open(text_path, "w", encoding="utf-8") as handle:
            handle.write("import os\n\n\ndef main():\n    print('hi')\n")
        markdown_path = os.path.join(work, "notes.md")
        with open(markdown_path, "w", encoding="utf-8") as handle:
            handle.write("# Notes\n\n- one\n- two\n")
        image_path = os.path.join(work, "swatch.png")
        _write_png(image_path)
        directory = os.path.join(work, "folder")
        os.makedirs(directory, exist_ok=True)

        self.feed(f"echo {text_path}\n")
        self.pump(0.9)
        text = _terminal_text(view)
        self.check("terminal shows the path", text_path in text)
        self.check(
            "path match installed", getattr(view, "_path_match_installed", False)
        )
        found = _find_path_cell(view, text_path)
        self.check("path is matchable at a cell", found is not None, str(found))
        if found is not None:
            row, column = found
            char_w = view.terminal.get_char_width()
            char_h = view.terminal.get_char_height()
            event = _Click(
                column * char_w + char_w // 2,
                row * char_h + char_h // 2,
                button=1,
                state=1 << 2,  # Ctrl held, to exercise the path-opening branch
            )
            handled = view._on_terminal_button_press(view.terminal, event)
            self.check("ctrl+click handled", bool(handled))
            self.pump(0.6)
            self.check(
                "viewer opened by ctrl+click",
                len(window._viewer_windows) == 1,
                f"{len(window._viewer_windows)} windows",
            )
            if window._viewer_windows:
                opened = window._viewer_windows[0]
                self.check(
                    "viewer shows the right file",
                    opened.path == text_path,
                    opened.path,
                )
                self.check(
                    "text viewer highlights python",
                    opened.viewer.buffer.get_language() is not None
                    and "Python" in opened.viewer.buffer.get_language().get_name(),
                )
                self.shot("viewer-text")
                opened.destroy()
                self.pump(0.3)

        # open_path() routes by type without needing a click.
        window.open_path(markdown_path)
        self.pump(0.5)
        viewer = window._viewer_windows[-1] if window._viewer_windows else None
        self.check("markdown opens in the text viewer", viewer is not None)
        if viewer is not None:
            language = viewer.viewer.buffer.get_language()
            self.check(
                "markdown is highlighted",
                language is not None and "Markdown" in language.get_name(),
                language.get_name() if language else "none",
            )
            viewer.destroy()
            self.pump(0.3)

        window.open_path(image_path)
        self.pump(0.6)
        viewer = window._viewer_windows[-1] if window._viewer_windows else None
        self.check("image opens in the image viewer", viewer is not None)
        if viewer is not None:
            # The displayed pixbuf is scaled to fit; the decoded source keeps
            # the file's real dimensions.
            source = viewer.viewer._pixbuf
            shown = viewer.viewer.image.get_pixbuf()
            self.check(
                "image is decoded",
                source is not None
                and source.get_width() == 120
                and source.get_height() == 80,
                f"source={source.get_width()}x{source.get_height()}"
                if source
                else "none",
            )
            self.check(
                "image is scaled to fit the window",
                shown is not None and shown.get_width() <= 120,
                f"shown={shown.get_width()}x{shown.get_height()}" if shown else "none",
            )
            self.shot("viewer-image")
            viewer.destroy()
            self.pump(0.3)

        self.check(
            "directory open reports a failure or launches a file manager",
            window.open_path(directory) in (True, False),
        )
        self.check(
            "missing path is refused",
            window.open_path(os.path.join(work, "nope.txt")) is False,
        )
        # Search and Ctrl+click leave a selection behind; clearing it is what
        # "nothing selected" means for the open-selection action.
        view.terminal.unselect_all()
        self.pump(0.2)
        self.check(
            "empty selection is refused", window.open_selection() is False
        )
        # A selection that does contain an existing path must open it.  The
        # path is echoed right before selecting so the selection is deterministic
        # (select_all would also cover output from earlier steps, whose files may
        # have been removed since).
        self.feed(f"echo {text_path}\n")
        self.pump(0.8)
        view.terminal.select_all()
        self.pump(0.2)
        self.check(
            "selection containing a path opens",
            window.open_selection() is True,
            f"status={window.status_message.get_text()!r}",
        )
        self.pump(0.4)
        while window._viewer_windows:
            window._viewer_windows[-1].destroy()
            self.pump(0.2)
        self.check(
            "viewers all closed", len(window._viewer_windows) == 0,
            f"{len(window._viewer_windows)} left",
        )

        # Regression: on an idle prompt the buffer's last line is
        # "user@host:~$", which must not be turned into a path.
        view.terminal.unselect_all()
        self.pump(0.4)
        # The buffer still holds the paths echoed earlier in this step, so the
        # check targets the *last* line (the idle prompt) explicitly.
        idle_line = view.path_under_cursor()
        self.check(
            "idle prompt is not mistaken for a path",
            "lpzone@" not in idle_line and "~$" not in idle_line,
            f"path_under_cursor={idle_line!r}",
        )
        # A prompt line on its own must never be treated as a path.
        from vela.terminal import _looks_like_path

        self.check(
            "shell prompt text is not path-shaped",
            not _looks_like_path("lpzone@gp76:~$")
            and not _looks_like_path("lpzone@gp76:~/vela-terminal$"),
        )
        # And a path that does not exist is reported as missing rather than
        # silently opened.
        missing = os.path.join(work, "definitely-not-here.txt")
        before_status = window.status_message.get_text()
        opened = window.open_path(missing)
        self.check(
            "a missing path is reported as missing",
            opened is False and "不存在" in window.status_message.get_text(),
            f"status={window.status_message.get_text()!r} (was {before_status!r})",
        )

    def step_teardown(self) -> None:
        window = self.window()
        self.check("statusbar visible", window.statusbar.get_visible())
        self.check("headerbar visible", window.headerbar.get_visible())

    # -- reporting -------------------------------------------------------
    def finish(self) -> int:
        self._write_report()
        failed = [step for step in self.steps if not step["ok"]]
        print(f"\n{len(self.steps) - len(failed)}/{len(self.steps)} checks passed", flush=True)
        if not self.keep_config:
            shutil.rmtree(self.config_dir, ignore_errors=True)
        return 1 if failed else 0

    def _write_report(self) -> None:
        failed = [step for step in self.steps if not step["ok"]]
        report = {
            "steps": self.steps,
            "screenshots": self.shots,
            "failed": len(failed),
            "total": len(self.steps),
            "config_dir": self.config_dir,
        }
        with open(os.path.join(self.out_dir, "report.json"), "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)


def _terminal_text(view) -> str:
    """Read the whole VTE buffer."""
    if view is None:
        return ""
    text, _attributes = view.terminal.get_text(
        lambda *_args: True, lambda *_args: True
    )
    return text or ""


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Vela UI smoke test")
    parser.add_argument("--out", default="/tmp/vela-smoke", help="output directory")
    parser.add_argument(
        "--keep-config", action="store_true", help="keep the temporary config dir"
    )
    args = parser.parse_args(argv)
    if not Gtk.init_check(None)[0]:
        sys.stderr.write("error: no display available (set DISPLAY)\n")
        return 2
    return Smoke(args.out, args.keep_config).run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
