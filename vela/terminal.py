"""The terminal view: one VTE widget plus everything we hang off it."""

from __future__ import annotations

import os
import re
import shlex
import signal
from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Pango", "1.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from . import theme as theme_mod  # noqa: E402
from . import terminalmenu  # noqa: E402

_CURSOR_SHAPES = {
    "block": Vte.CursorShape.BLOCK,
    "ibeam": Vte.CursorShape.IBEAM,
    "underline": Vte.CursorShape.UNDERLINE,
}

_CURSOR_BLINK = {
    "off": Vte.CursorBlinkMode.OFF,
    "on": Vte.CursorBlinkMode.ON,
    "system": Vte.CursorBlinkMode.SYSTEM,
}

_ESCAPE_CHARS = set(" \t\n\\'\"`$!*?[](){}<>|&;#~^")

# Anything that looks like a filesystem path, so Ctrl+click can open it.
#
# Two forms are recognised: a path with at least one directory separator, and a
# bare filename with a known extension.  The lookbehind stops the path branch
# from biting a slice out of a URL ("https://host/a.txt" must not become
# "/a.txt"), and the filename branch requires a start-of-word boundary so
# "src/main.c" is matched whole rather than as "/main.c".
_FILE_EXTENSIONS = (
    "txt|text|log|md|markdown|rst|json|jsonc|ya?ml|toml|ini|cfg|conf|env"
    "|py|pyi|sh|bash|zsh|fish|c|h|cc|cpp|cxx|hpp|java|kt|go|rs|swift|cs|rb|php|pl|lua|r"
    "|js|mjs|cjs|jsx|ts|tsx|vue|svelte|css|scss|less|html?|xml|sql|graphql"
    "|csv|tsv|patch|diff|desktop|service|cmake|mk|properties"
    "|png|jpe?g|gif|bmp|webp|svg|ico|tiff?|pdf|zip|tar|gz|xz|deb|rpm"
)

PATH_PATTERN = (
    # "~/" or "./" or "../" or a plain absolute path
    r"(?<![\w:~])~/[\w.@%+\-]+(?:/[\w.@%+\-]+)*/?"
    r"|(?<![\w:])\.{1,2}/[\w.@%+\-]+(?:/[\w.@%+\-]+)*/?"
    # a relative path that contains a directory separator
    r"|(?<![\w@%+\-:/.])[\w@%+\-]+(?:/[\w.@%+\-]+)+/?"
    r"|(?<![\w:/~])/[\w.@%+\-]+(?:/[\w.@%+\-]+)*/?"
    # a bare filename with a known extension, optionally compounded
    r"|(?<![\w@%+\-./])[\w@%+\-]+\.(?:" + _FILE_EXTENSIONS + r")"
    r"(?:\.(?:gz|bz2|xz|zst|zip|7z))?(?![\w])"
)

_PCRE2_MULTILINE = 0x00000400
_PCRE2_UTF8 = 0x00080000
_MATCH_FLAGS = _PCRE2_MULTILINE | _PCRE2_UTF8

# VTE reserves tag 0 for its own PCRE2 matches; the tag passed to
# match_add_regex() is a *group number* within the pattern, so a pattern with no
# capture groups always reports tag 0.
_MATCH_TAG_PATH = 0

# VTE 0.60 rejects a search regex unless PCRE2_MULTILINE was compiled in.
SEARCH_FLAGS = Vte.REGEX_FLAGS_DEFAULT | _PCRE2_MULTILINE


def _rgba(color: str, alpha: float = 1.0) -> Gdk.RGBA:
    value = Gdk.RGBA()
    if not value.parse(color):
        value.parse("#000000")
    value.alpha = alpha
    return value


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


class TerminalView(Gtk.Box):
    """A single terminal pane.

    Signals:
        title-changed (str): the child process changed its window title.
        directory-changed (str): the shell moved to another directory.
        child-exited (int): the shell exited with the given status.
        focus-requested: the user clicked inside the pane.
    """

    __gsignals__ = {
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "directory-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "child-exited": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        "focus-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "cursor-moved": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        "open-path": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        "run-action": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config, theme: theme_mod.Theme, notify: Optional[Callable] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.config = config
        self.theme = theme
        self._notify = notify or (lambda *_args, **_kwargs: None)
        self.exited = False
        self.exit_status: Optional[int] = None
        self._title = ""
        self._directory = ""
        self._child_pid = 0
        self._bell_source = 0
        self.focused = False
        self._border_color = theme.accent

        self.get_style_context().add_class("vela-pane")
        # The focus ring is drawn by hand: a GTK box-shadow on a Gtk.Box is not
        # painted (the widget has no background of its own), which left the
        # focused pane looking identical to the others.
        # Connected with after=True so the ring is painted on top of the child
        # widgets; a normal handler runs before them and the terminal's opaque
        # background would cover the border completely.
        self.connect_after("draw", self._on_draw_border)

        self.terminal = Vte.Terminal()
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)
        self.terminal.set_rewrap_on_resize(True)
        self.terminal.set_scroll_on_output(
            bool(self.config.get("behavior.scroll_on_output"))
        )
        self.terminal.set_scroll_on_keystroke(
            bool(self.config.get("behavior.scroll_on_keystroke"))
        )
        self.terminal.set_allow_hyperlink(bool(self.config.get("behavior.allow_hyperlink")))
        self.terminal.set_mouse_autohide(
            bool(self.config.get("appearance.hide_mouse_when_typing"))
        )
        self.terminal.set_word_char_exceptions(
            str(self.config.get("behavior.word_char_exceptions"))
        )
        self.terminal.set_audible_bell(bool(self.config.get("behavior.audible_bell")))
        try:
            self.terminal.set_encoding("UTF-8")
        except GLib.Error:
            pass

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.get_style_context().add_class("vela-scroll")
        self._scrolled.add(self.terminal)
        self.pack_start(self._scrolled, True, True, 0)

        self.connect("button-press-event", self._on_button_press)
        # VTE consumes the left button itself (selection, dragging the cursor to
        # the clicked cell), so a click never bubbles up to the pane.  Handling
        # it on the terminal with connect() runs *before* VTE's own handler:
        # focus is switched, then False lets VTE do its normal thing.
        self.terminal.connect("button-press-event", self._on_terminal_button_press)
        self.terminal.connect("button-release-event", self._on_terminal_button_release)
        # Ctrl+click is handled on the terminal itself: events delivered there
        # already carry terminal-relative coordinates, which is exactly what
        # match_check() needs.  Translating from the pane is unreliable inside a
        # scrolled container.
        self.terminal.connect("child-exited", self._on_child_exited)
        self.terminal.connect("window-title-changed", self._on_title_changed)
        self.terminal.connect(
            "current-directory-uri-changed", self._on_directory_changed
        )
        self.terminal.connect("bell", self._on_bell)
        self.terminal.connect("selection-changed", self._on_selection_changed)
        self.terminal.connect("cursor-moved", self._on_cursor_moved)
        self._path_match_id = 0
        self._path_match_installed = False
        # VTE cannot register a match regex before the terminal is realized, so
        # the install is retried once the widget gets a window.
        self.terminal.connect("realize", lambda *_: self._install_path_matches())
        # Hiding and re-showing the window destroys and recreates the terminal's
        # GdkWindow, which drops registered matches; reinstall on map.
        self.terminal.connect("map", lambda *_: self._install_path_matches())

        self.apply_theme(theme)

    def _install_path_matches(self) -> None:
        """Mark filesystem paths so Ctrl+click can open them.

        Called on realize and on map: hiding a window tears the terminal's
        GdkWindow down and the registered matches go with it, so this has to be
        able to run again for the same widget.
        """
        if not self.config.get("files.ctrl_click_paths"):
            return
        if self._path_match_installed and self.terminal.get_mapped():
            return
        try:
            # match_add_regex() only accepts a regex compiled for matching;
            # a search-purpose regex trips a VTE assertion and matches nothing.
            regex = Vte.Regex.new_for_match(PATH_PATTERN, -1, _MATCH_FLAGS)
            self._path_match_id = self.terminal.match_add_regex(
                regex, _MATCH_TAG_PATH
            )
            # Match ids start at 0, so the id itself cannot signal success.
            self._path_match_installed = True
        except GLib.Error:
            self._path_match_id = 0
            self._path_match_installed = False

    # -- setup -----------------------------------------------------------
    def set_focused(self, focused: bool) -> None:
        """Show or hide the focus ring around this pane."""
        if bool(focused) == self.focused:
            return
        self.focused = bool(focused)
        self.queue_draw()

    def _on_draw_border(self, widget, cr) -> bool:
        """Draw a thin accent border when this pane has the focus.

        Runs after the child widgets have painted, so the ring sits on top of
        the terminal rather than behind it.
        """
        if not self.focused:
            return False
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 2 or height <= 2:
            return False
        red, green, blue = _rgb_floats(self._border_color)
        cr.save()
        cr.set_source_rgba(red, green, blue, 0.9)
        # Half-pixel offsets keep the 1px line crisp instead of blurring across
        # two device pixels.
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, width - 1.0, height - 1.0)
        cr.stroke()
        cr.restore()
        return False

    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        self._border_color = theme.accent
        self.queue_draw()
        palette = [_rgba(color) for color in theme.ansi()]
        self.terminal.set_colors(
            _rgba(theme.foreground), _rgba(theme.background), palette
        )
        self.terminal.set_color_cursor(_rgba(theme.cursor))
        self.terminal.set_color_cursor_foreground(_rgba(theme.cursor_text))
        self.terminal.set_color_highlight(_rgba(theme.selection))
        self.terminal.set_color_highlight_foreground(_rgba(theme.selection_text))
        self.terminal.set_color_bold(_rgba(theme.palette.get("bright_white", theme.foreground)))
        opacity = float(self.config.get("appearance.opacity"))
        self.terminal.set_opacity(opacity if opacity < 1.0 else 1.0)
        self.apply_appearance()

    def apply_appearance(self) -> None:
        appearance = self.config.section("appearance")
        if appearance.get("use_system_font"):
            description = self._system_monospace_font()
        else:
            family = appearance.get("font_family") or "monospace"
            size = float(appearance.get("font_size") or 12.0)
            # Pango accepts a comma-separated fallback list: the first family
            # that has a glyph wins, so Latin can come from a programming font
            # while CJK falls through to a dedicated font.
            fallback = str(appearance.get("fallback_font") or "").strip()
            families = family if not fallback else f"{family}, {fallback}"
            description = Pango.FontDescription.from_string(f"{families} {size}")
        self.terminal.set_font(description)
        self.terminal.set_allow_bold(bool(appearance.get("allow_bold")))
        self.terminal.set_bold_is_bright(bool(appearance.get("bold_is_bright")))
        self.terminal.set_cursor_shape(
            _CURSOR_SHAPES.get(appearance.get("cursor_shape"), Vte.CursorShape.BLOCK)
        )
        self.terminal.set_cursor_blink_mode(
            _CURSOR_BLINK.get(appearance.get("cursor_blink"), Vte.CursorBlinkMode.SYSTEM)
        )
        self.terminal.set_scrollback_lines(int(appearance.get("scrollback_lines")))
        self.terminal.set_cell_width_scale(float(appearance.get("cell_width_scale")))
        self.terminal.set_cell_height_scale(float(appearance.get("cell_height_scale")))
        padding = int(appearance.get("padding"))
        self.terminal.set_margin_start(padding)
        self.terminal.set_margin_end(padding)
        self.terminal.set_margin_top(padding)
        self.terminal.set_margin_bottom(padding)

    def _system_monospace_font(self) -> Pango.FontDescription:
        settings = Gtk.Settings.get_default()
        if settings is not None:
            name = settings.get_property("gtk-font-name")
            if name:
                return Pango.FontDescription.from_string(name)
        return Pango.FontDescription.from_string("monospace 12")

    # -- shell -----------------------------------------------------------
    def spawn(self, argv: Optional[List[str]] = None, cwd: Optional[str] = None) -> None:
        command = list(argv) if argv else self.default_argv()
        working_directory = cwd
        if not working_directory:
            configured = str(self.config.get("behavior.working_directory") or "")
            working_directory = os.path.expanduser(configured) if configured else None
        if working_directory and not os.path.isdir(working_directory):
            self._notify(
                f"工作目录不存在，已改用主目录：{working_directory}", 6.0, "warning"
            )
            working_directory = None
        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                working_directory,
                command,
                None,
                # SEARCH_PATH lets argv[0] be a bare command name such as
                # "htop" (used by --execute); without it only absolute paths
                # would work.
                GLib.SpawnFlags.DEFAULT | GLib.SpawnFlags.SEARCH_PATH,
                None,
                None,
                -1,
                None,
                self._on_spawned,
                None,
            )
        except GLib.Error as error:
            message = f"无法启动 shell：{error.message}"
            self.show_message(message)
            self._notify(message, 8.0, "error")
        except Exception as error:  # pragma: no cover - defensive
            self.show_message(f"无法启动 shell：{error}")

    def _on_spawned(self, _terminal, pid: int, error, _user_data) -> None:
        if error is not None:
            message = f"无法启动 shell：{error.message}"
            self._notify(message, 8.0, "error")
            self.show_message(message)
            return
        self._child_pid = int(pid) if pid and pid > 0 else 0

    def default_argv(self) -> List[str]:
        configured = str(self.config.get("behavior.shell") or "").strip()
        if configured:
            try:
                argv = shlex.split(configured)
            except ValueError:
                argv = [configured]
            if argv:
                if self.config.get("behavior.login_shell") and os.path.basename(argv[0]):
                    argv[0] = "-" + os.path.basename(argv[0])
                return argv
        shell = os.environ.get("SHELL") or "/bin/bash"
        if self.config.get("behavior.login_shell"):
            shell = "-" + shell
        return [shell]

    def show_message(self, text: str) -> None:
        """Write a message into the terminal buffer (used for our own errors)."""
        safe = text.replace("\r", "").replace("\n", "\r\n")
        self.terminal.feed_child(safe.encode("utf-8"))

    # -- input helpers ---------------------------------------------------
    def send_text(self, text: str) -> None:
        self.terminal.feed_child(text.encode("utf-8"))

    def send_key(self, keyval: int, modifiers: Gdk.ModifierType = 0) -> None:
        """Synthesise a key press on the terminal (e.g. Ctrl+L)."""
        event = Gdk.EventKey.new(Gdk.EventType.KEY_PRESS)
        event.keyval = keyval
        event.state = modifiers
        event.hardware_keycode = 0
        event.group = 0
        self.terminal.emit("key-press-event", event)

    def send_control(self, letter: str) -> None:
        value = ord(letter.upper()) - 64
        self.send_text(chr(value))

    def copy(self) -> None:
        if self.terminal.get_has_selection():
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)

    def paste(self) -> None:
        self.terminal.paste_clipboard()

    def paste_escaped(self) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if not text:
            return
        self.send_text(_escape_for_shell(text))

    def select_all(self) -> None:
        self.terminal.select_all()

    def clear_screen(self) -> None:
        self.send_control("l")

    def reset_terminal(self) -> None:
        self.send_text("\x1bc")

    def scroll(self, delta: int) -> None:
        """Scroll the viewport: negative is up."""
        adjustment = self._scrolled.get_vadjustment()
        if adjustment is None:
            return
        step = adjustment.get_step_increment() or 20.0
        target = adjustment.get_value() + delta * step * 3
        target = max(adjustment.get_lower(), min(adjustment.get_upper() - adjustment.get_page_size(), target))
        adjustment.set_value(target)

    def scroll_to_bottom(self) -> None:
        self.send_text("")
        adjustment = self._scrolled.get_vadjustment()
        if adjustment is not None:
            adjustment.set_value(adjustment.get_upper())

    # -- search ----------------------------------------------------------
    def search(self, pattern: str, regex: bool = False, forward: bool = True) -> bool:
        if not pattern:
            self.clear_search()
            return False
        if regex:
            try:
                compiled = Vte.Regex.new_for_search(
                    pattern, -1, SEARCH_FLAGS
                )
            except GLib.Error:
                compiled = None
            if compiled is None:
                return False
        else:
            compiled = Vte.Regex.new_for_search(
                re.escape(pattern), -1, SEARCH_FLAGS
            )
        self.terminal.search_set_regex(compiled, 0)
        if forward:
            return bool(self.terminal.search_find_next())
        return bool(self.terminal.search_find_previous())

    def clear_search(self) -> None:
        self.terminal.search_set_regex(None, 0)

    def get_selected_text(self) -> str:
        text, _attributes = self.terminal.get_text(
            lambda *_: True, lambda *_: True
        )
        return text or ""

    def get_all_text(self) -> str:
        """Whole scrollback buffer as text."""
        return self.get_selected_text()

    # -- info ------------------------------------------------------------
    @property
    def title(self) -> str:
        return self._title or os.path.basename(self.shell_name) or "终端"

    @property
    def shell_name(self) -> str:
        argv = self.default_argv()
        return os.path.basename(argv[0].lstrip("-")) if argv else "shell"

    @property
    def directory(self) -> str:
        return self._directory

    @property
    def child_pid(self) -> int:
        return self._child_pid

    @property
    def has_foreground_process(self) -> bool:
        pid = self.child_pid
        if not pid:
            return False
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as handle:
                stat = handle.read()
        except OSError:
            return False
        try:
            fields = stat.rsplit(")", 1)[1].split()
            foreground_pgid = int(fields[2])
        except (IndexError, ValueError):
            return False
        return foreground_pgid != pid

    # -- signals ---------------------------------------------------------
    def _on_button_press(self, _widget, event) -> bool:
        """Catch presses that reach the pane itself (padding, scrollbar area).

        The terminal handles clicks inside the text area; this only fires for the
        surrounding frame, and all it does is focus the pane.
        """
        self.emit("focus-requested")
        return False

    def _on_terminal_button_press(self, _widget, event) -> bool:
        """Handle a press on the terminal: focus, Ctrl+click paths, right menu.

        Returning ``False`` lets VTE process the event normally (text selection,
        moving the cursor, mouse reporting to the running program), which is why
        only the cases we fully own return ``True``.
        """
        # Any button press focuses the pane first, so clicking a split always
        # makes it the active one.
        self.emit("focus-requested")

        if event.button == 1:
            if not self._path_match_installed:
                return False
            if not (event.state & Gdk.ModifierType.CONTROL_MASK):
                return False
            matched = self._path_at(int(event.x), int(event.y))
            if not matched:
                return False
            reveal = bool(event.state & Gdk.ModifierType.SHIFT_MASK) and bool(
                self.config.get("files.reveal_on_ctrl_shift_click")
            )
            self.emit("open-path", matched, reveal)
            return True

        if event.button == 3:
            self._show_context_menu(event)
            return True

        return False

    def _on_terminal_button_release(self, _widget, event) -> bool:
        """Middle-click paste, matching the convention in other terminals."""
        if event.button == 2 and self.config.get("behavior.paste_on_middle_click"):
            primary = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
            text = primary.wait_for_text()
            if text:
                self.send_text(text)
                return True
        return False

    def _show_context_menu(self, event) -> None:
        """Pop up the terminal context menu at the pointer."""
        items = terminalmenu.menu_items(
            has_selection=self.terminal.get_has_selection(),
            pane_count=self._pane_count(),
            zoomed=self._pane_zoomed(),
        )
        menu = terminalmenu.build_menu(items, self._run_menu_action, self._accel_hint)
        self._context_menu = menu
        menu.connect("hide", lambda *_: setattr(self, "_context_menu", None))
        try:
            menu.popup_at_pointer(event)
        except (TypeError, AttributeError):
            # Callers may pass a stand-in event (tests); fall back to positioning
            # at the widget, which still gives a usable menu.
            menu.popup_at_widget(
                self.terminal, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, None
            )

    def _run_menu_action(self, action: str) -> None:
        self.emit("run-action", action)

    def _accel_hint(self, config_key: str) -> str:
        """Human-readable shortcut for a menu entry, if one is bound."""
        from . import keymap

        return keymap.display(str(self.config.get(f"keybindings.{config_key}") or ""))

    def _pane_count(self) -> int:
        getter = getattr(self, "pane_count", None)
        try:
            return int(getter()) if getter is not None else 1
        except Exception:  # pragma: no cover - defensive
            return 1

    def _pane_zoomed(self) -> bool:
        getter = getattr(self, "pane_zoomed", None)
        try:
            return bool(getter()) if getter is not None else False
        except Exception:  # pragma: no cover - defensive
            return False

    def _path_at(self, x: int, y: int) -> str:
        """Path under a pixel position, or ``""``.

        The position is converted to a cell and looked up with
        ``match_check()``: that path is deterministic and, unlike
        ``match_check_event()``, does not depend on event construction details.
        """
        if not self._path_match_installed:
            return ""
        width = self.terminal.get_char_width()
        height = self.terminal.get_char_height()
        if width <= 0 or height <= 0:
            return ""
        column = int(x // width)
        row = int(y // height)
        if not (0 <= column < self.terminal.get_column_count()):
            return ""
        if not (0 <= row < self.terminal.get_row_count()):
            return ""
        result = self.terminal.match_check(column, row)
        if not result:
            return ""
        matched, tag = result
        if tag != _MATCH_TAG_PATH or not matched:
            return ""
        return matched

    def path_under_cursor(self) -> str:
        """A path the user is most likely pointing at, or ``""``.

        Only text that actually resolves to something on disk is returned.  The
        last line is never used verbatim: on an idle prompt it is something like
        ``lpzone@gp76:~$``, which would otherwise be turned into the nonsense
        path ``/home/lpzone/lpzone@gp76:~$``.

        Order: an existing path inside the selection, then an existing path on
        one of the last few lines (where the prompt and command are), then a
        selection that looks like a path.  VTE reports the cursor row relative
        to the visible screen while ``get_text`` returns the whole scrollback,
        so line arithmetic on the cursor would be unreliable.
        """
        from . import files as files_mod

        selection = self.get_selected_text().strip()
        if selection and _resolves(selection):
            return selection
        lines = [line for line in self.get_all_text().splitlines() if line.strip()]
        for line in reversed(lines[-3:]):
            candidates = files_mod.candidate_paths(line)
            if candidates:
                return candidates[-1]
        # A selection that is itself a path (possibly not yet created) is still
        # worth honouring, so the caller can report "path does not exist".
        if selection and _looks_like_path(selection):
            return selection
        return ""

    def process_usage(self):
        """CPU/RSS for this pane's shell, for the sysinfo panel."""
        from .sysinfo import ProcessUsage

        pid = self.child_pid
        if not pid or self.exited:
            return ProcessUsage(pid=pid, available=False)
        return _shared_sampler().process(pid)

    def _on_child_exited(self, _terminal, status: int) -> None:
        self.exited = True
        self.exit_status = status
        if self.config.get("behavior.show_exit_hint"):
            code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else status
            self.show_message(f"\r\n[进程已退出，状态码 {code}]\r\n")
        self.emit("child-exited", status)

    def _on_title_changed(self, terminal) -> None:
        title = terminal.get_window_title() or ""
        if title != self._title:
            self._title = title
            self.emit("title-changed", title)

    def _on_directory_changed(self, terminal) -> None:
        uri = terminal.get_current_directory_uri() or ""
        if not uri:
            return
        path = GLib.filename_from_uri(uri)[0] or ""
        if path != self._directory:
            self._directory = path
            self.emit("directory-changed", path)

    def _on_bell(self, _terminal) -> None:
        if not self.config.get("behavior.visual_bell"):
            return
        context = self.get_style_context()
        context.add_class("vela-bell")
        if self._bell_source:
            GLib.source_remove(self._bell_source)
        self._bell_source = GLib.timeout_add(120, self._clear_bell)

    def _clear_bell(self) -> bool:
        self._bell_source = 0
        self.get_style_context().remove_class("vela-bell")
        return False

    def _on_selection_changed(self, _terminal) -> None:
        if self.config.get("behavior.copy_on_select") and self.terminal.get_has_selection():
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)

    def _on_cursor_moved(self, terminal) -> None:
        try:
            column, row = terminal.get_cursor_position()
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        self.emit("cursor-moved", int(column), int(row))

    # -- teardown --------------------------------------------------------
    def terminate(self) -> None:
        if self.exited:
            return
        pid = self.child_pid
        if not pid:
            return
        try:
            os.kill(pid, signal.SIGHUP)
        except OSError:
            return


_SAMPLER = None


def _shared_sampler():
    """One sampler for the whole process: it only reads /proc on demand."""
    global _SAMPLER
    if _SAMPLER is None:
        from .sysinfo import Sampler

        _SAMPLER = Sampler()
    return _SAMPLER


def _resolves(text: str) -> bool:
    try:
        return os.path.exists(os.path.expanduser(os.path.expandvars(text)))
    except (OSError, ValueError):
        return False


# Characters that make a string plausible as a path rather than, say, a shell
# prompt or an English sentence.
_PATH_ISH = re.compile(r"^(?:~|\.{1,2})?(?:/[\w.@%+\-]+)+/?$")


def _looks_like_path(text: str) -> bool:
    """Whether ``text`` is shaped like a filesystem path (existence not required)."""
    value = text.strip().strip("'\"")
    if not value or len(value) > 4096 or "\n" in value:
        return False
    if value in ("~", "/"):
        return True
    if value.startswith("~") or value.startswith("/"):
        return bool(_PATH_ISH.match(value)) or "/" in value[1:]
    if value.startswith("."):
        # ./, ../, ./a/b — anything relative and clearly path-shaped.
        return value.startswith("./") or value.startswith("../")
    return False


def _escape_for_shell(text: str) -> str:
    """Backslash-escape shell metacharacters, preserving newlines as escapes."""
    out: List[str] = []
    for char in text:
        if char == "\n":
            out.append("\\\n")
        elif char in _ESCAPE_CHARS:
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)
