"""File browser side panel: breadcrumbs, a three-column listing and a toolbar.

Layout follows what a desktop file manager shows: name, permissions and
modification time, with directories listed first and a ``..`` row to go up.
Opening a file reuses :mod:`vela.files`, so text and images open in Vela's own
viewer while everything else goes to the desktop's default application.
"""

from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import files, fsmodel  # noqa: E402
from . import theme as theme_mod  # noqa: E402

COL_NAME = 0
COL_PERM = 1
COL_MODIFIED = 2
COL_SIZE = 3
COL_ICON = 4
COL_PATH = 5
COL_IS_DIR = 6
COL_TOOLTIP = 7

#: ``cd`` flags that mean "the previous directory"; handled as a special case
#: rather than as a path, because ``-`` is not a legal directory name here.
_CD_BACK = "-"


def parse_cd(text: str) -> Optional[str]:
    """The path a command-bar line asks for, or ``None`` if it is not a ``cd``.

    Accepted forms are ``cd`` on its own (home), ``cd -`` (previous directory),
    ``cd PATH`` and a bare ``PATH``.  Quoting is honoured so a directory with
    spaces can be reached.  This is deliberately not a shell: no ``&&``, no
    globbing, no expansion of anything except ``~`` and ``$VAR`` in the path.
    """
    text = text.strip()
    if not text:
        return None
    tokens = _split_command(text)
    if tokens is None:
        return None
    if not tokens:
        return None
    if tokens[0] == "cd":
        rest = tokens[1:]
    elif len(tokens) == 1 and _looks_like_path(tokens[0]):
        # A bare path is the common case, so it is accepted as a shortcut.
        rest = tokens
    else:
        return None
    if not rest:
        return "~"
    if len(rest) > 1:
        return None
    return rest[0]


def _looks_like_path(token: str) -> bool:
    """Whether a bare word is meant as a path rather than a command.

    Without this, ``ls`` would be read as "go to the directory named ls" and the
    bar would quietly do something the user did not ask for.
    """
    if not token:
        return False
    if token in (_CD_BACK, "~", "..", ".") or token.startswith(("~", "/", "./", "../")):
        return True
    return "/" in token


def split_for_completion(text: str) -> Tuple[str, str, str]:
    """Split a half-typed line into ``(prefix, base, fragment)``.

    ``prefix`` is what has to stay in front of the completed name (``"cd "`` or
    an empty string), ``base`` is the directory the fragment is resolved against
    (a raw, unexpanded string so it can be shown back to the user), and
    ``fragment`` is the part being completed.  Completion works on the raw text
    rather than on ``parse_cd``'s output so that what the user typed keeps its
    own spelling: completing ``cd ~/ve`` must produce ``cd ~/vela-terminal``, not
    an absolute path.
    """
    stripped = text.strip()
    if not stripped:
        return "", "", ""
    if stripped == "cd":
        # The space is added as part of the completion, so Tab right after "cd"
        # produces "cd name/" rather than gluing the name onto the command.
        return "cd ", "", ""
    if len(stripped) > 2 and stripped.startswith("cd") and stripped[2].isspace():
        prefix = "cd "
        rest = stripped[3:].strip()
    else:
        prefix, rest = "", stripped
    if not rest:
        return prefix, "", ""
    if "/" in rest:
        base, _, fragment = rest.rpartition("/")
        return prefix, base + "/", fragment
    return prefix, "", rest


def completion_candidates(base: str, fragment: str, current: str) -> List[str]:
    """Directory names under ``base`` that start with ``fragment``.

    Only directories are offered: this bar navigates, so completing a file name
    would produce something that cannot be entered.  Hidden directories are
    offered only once the fragment itself starts with a dot, which is how shells
    behave.
    """
    raw = base or current
    expanded = os.path.expanduser(os.path.expandvars(raw)) if raw else ""
    if not expanded:
        expanded = current or os.path.expanduser("~")
    if not os.path.isabs(expanded):
        expanded = os.path.join(current or os.path.expanduser("~"), expanded)
    directory = os.path.normpath(expanded)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    candidates = []
    for name in names:
        if not name.startswith(fragment):
            continue
        if name.startswith(".") and not fragment.startswith("."):
            continue
        if os.path.isdir(os.path.join(directory, name)):
            candidates.append(name)
    return sorted(candidates)


def _common_prefix(names: List[str]) -> str:
    if not names:
        return ""
    shortest = min(names, key=len)
    for index, char in enumerate(shortest):
        if any(name[index] != char for name in names):
            return shortest[:index]
    return shortest


def _split_command(text: str) -> Optional[List[str]]:
    """Split on whitespace, honouring single and double quotes."""
    tokens: List[str] = []
    current: List[str] = []
    quote = ""
    started = False
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
            continue
        if char in ("'", '"'):
            quote = char
            started = True
            continue
        if char.isspace():
            if started or current:
                tokens.append("".join(current))
                current = []
                started = False
            continue
        current.append(char)
        started = True
    if quote:
        # An unterminated quote is a typo, not a path.
        return None
    if started or current:
        tokens.append("".join(current))
    return tokens


class FileBrowserPanel(Gtk.Box):
    """A directory listing that can follow the terminal's working directory."""

    def __init__(
        self,
        config,
        theme: theme_mod.Theme,
        on_open: Optional[Callable[[str], bool]] = None,
        notify: Optional[Callable] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.config = config
        self.theme = theme
        self._on_open = on_open
        self._notify = notify or (lambda *_args, **_kwargs: None)
        self.directory = ""
        self.following = True
        self._previous_directory = ""
        self.show_hidden = bool(config.get("filebrowser.show_hidden"))
        self._icon_cache = {}
        self._up_row: Optional[Gtk.TreeIter] = None

        self.get_style_context().add_class("vela-filebrowser")
        # Width belongs to the shared side stack, not to an individual panel.
        self.set_size_request(200, -1)
        self._build()
        self.apply_theme(theme)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build(self) -> None:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        title = Gtk.Label(label="文件")
        title.set_xalign(0.0)
        title.get_style_context().add_class("vela-panel-title")
        header.pack_start(title, False, False, 0)

        self._follow_button = self._tool_button(
            "emblem-synchronizing-symbolic",
            "跟随终端目录",
            self.toggle_follow,
        )
        self._hidden_button = self._tool_button(
            "view-conceal-symbolic", "显示隐藏文件", self.toggle_hidden
        )
        self._up_button = self._tool_button(
            "go-up-symbolic", "上级目录", self.go_up
        )
        self._home_button = self._tool_button(
            "go-home-symbolic", "主目录", self.go_home
        )
        self._refresh_button = self._tool_button(
            "view-refresh-symbolic", "刷新", self.refresh
        )
        for button in (
            self._follow_button,
            self._hidden_button,
            self._up_button,
            self._home_button,
            self._refresh_button,
        ):
            header.pack_end(button, False, False, 0)
        self.pack_start(header, False, False, 0)

        self._breadcrumb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        crumb_scroller = Gtk.ScrolledWindow()
        crumb_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        crumb_scroller.set_propagate_natural_height(True)
        crumb_scroller.get_style_context().add_class("vela-crumbs")
        crumb_scroller.add(self._breadcrumb)
        self.pack_start(crumb_scroller, False, False, 0)

        self.store = Gtk.ListStore(
            str,  # name
            str,  # permissions
            str,  # modified
            str,  # size
            GdkPixbuf.Pixbuf,  # icon
            str,  # path
            bool,  # is_dir
            str,  # tooltip
        )
        self.view = Gtk.TreeView(model=self.store)
        self.view.set_headers_visible(True)
        self.view.set_enable_search(False)
        self.view.set_tooltip_column(COL_TOOLTIP)
        self.view.get_style_context().add_class("vela-filelist")
        self.view.connect("row-activated", self._on_row_activated)
        self.view.connect("button-press-event", self._on_button_press)
        self.view.connect("key-press-event", self._on_key_press)

        icon_renderer = Gtk.CellRendererPixbuf()
        name_renderer = Gtk.CellRendererText()
        name_renderer.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)
        name_column = Gtk.TreeViewColumn("名称")
        name_column.pack_start(icon_renderer, False)
        name_column.add_attribute(icon_renderer, "pixbuf", COL_ICON)
        name_column.pack_start(name_renderer, True)
        name_column.add_attribute(name_renderer, "text", COL_NAME)
        name_column.set_expand(True)
        name_column.set_resizable(True)
        # Keep the minimum small so 权限/修改时间 are never pushed off-screen in
        # a narrow sidebar; the name column ellipsises instead.
        name_column.set_min_width(90)
        name_column.set_sort_column_id(COL_NAME)
        self.view.append_column(name_column)

        perm_renderer = Gtk.CellRendererText()
        perm_renderer.set_property("family", "monospace")
        perm_renderer.set_property("scale", 0.9)
        perm_column = Gtk.TreeViewColumn("权限", perm_renderer, text=COL_PERM)
        perm_column.set_resizable(True)
        perm_column.set_min_width(80)
        perm_column.set_sort_column_id(COL_PERM)
        self.view.append_column(perm_column)

        time_renderer = Gtk.CellRendererText()
        time_renderer.set_property("scale", 0.9)
        time_column = Gtk.TreeViewColumn("修改时间", time_renderer, text=COL_MODIFIED)
        time_column.set_resizable(True)
        time_column.set_min_width(84)
        time_column.set_sort_column_id(COL_MODIFIED)
        self.view.append_column(time_column)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.get_style_context().add_class("vela-scroll")
        scroller.add(self.view)
        self.pack_start(scroller, True, True, 0)
        self._scroller = scroller

        self._summary = Gtk.Label(label="")
        self._summary.set_xalign(0.0)
        self._summary.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._summary.get_style_context().add_class("vela-dim")
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_border_width(6)
        footer.pack_start(self._summary, True, True, 0)
        self.pack_start(footer, False, False, 0)

        self._build_command_bar()
        self._sync_toolbar()

    def _build_command_bar(self) -> None:
        """A one-line ``cd`` prompt under the listing.

        This is a directory jump box, not a shell: it understands ``cd`` and
        nothing else, and it never touches the terminal.  Running arbitrary
        commands here would mean either hijacking the terminal (surprising, and
        it fights with whatever the user is doing there) or keeping a hidden
        shell alive for no benefit.  Anything beyond navigation belongs in the
        terminal itself.
        """
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.set_border_width(4)
        bar.get_style_context().add_class("vela-command-bar")

        prompt = Gtk.Label(label="$")
        prompt.get_style_context().add_class("vela-command-prompt")
        bar.pack_start(prompt, False, False, 0)

        self.command_entry = Gtk.Entry()
        self.command_entry.set_placeholder_text("输入路径或 cd 进入目录")
        self.command_entry.set_has_frame(False)
        self.command_entry.get_style_context().add_class("vela-command-entry")
        self.command_entry.connect("activate", self._on_command_activate)
        self.command_entry.connect("key-press-event", self._on_command_key)
        bar.pack_start(self.command_entry, True, True, 0)

        run_button = self._tool_button(
            "system-run-symbolic", "执行命令", self.run_command
        )
        bar.pack_end(run_button, False, False, 0)
        self.pack_start(bar, False, False, 0)
        self._command_history: List[str] = []
        self._history_index: Optional[int] = None

    # ------------------------------------------------------------------
    # command bar
    # ------------------------------------------------------------------
    def focus_command_bar(self) -> None:
        self.command_entry.grab_focus()

    def run_command(self) -> None:
        """Interpret the entry as a directory to go to."""
        text = self.command_entry.get_text().strip()
        if not text:
            return
        target = parse_cd(text)
        if target is None:
            self._notify("这里只支持 cd 到目录，例如：cd ~/project", 4, "warning")
            return
        path = self._resolve(target)
        if path is None:
            return
        self._command_history.append(text)
        self._history_index = None
        self.command_entry.set_text("")
        # Going somewhere is an instruction for this panel, so it stops
        # following the terminal — same as clicking a directory in the list.
        self.navigate(path)

    def _resolve(self, target: str) -> Optional[str]:
        """Turn a user-typed path into an absolute directory, or explain why not."""
        if target == _CD_BACK:
            previous = self._previous_directory
            if not previous or not os.path.isdir(previous):
                self._notify("没有上一个目录可返回", 4, "warning")
                return None
            return previous
        expanded = os.path.expanduser(os.path.expandvars(target))
        if not os.path.isabs(expanded):
            base = self.directory or os.path.expanduser("~")
            expanded = os.path.join(base, expanded)
        path = os.path.normpath(expanded)
        if not os.path.exists(path):
            self._notify(f"目录不存在：{path}", 4, "warning")
            return None
        if not os.path.isdir(path):
            self._notify(f"不是目录：{path}", 4, "warning")
            return None
        return path

    def _on_command_activate(self, _entry) -> None:
        self.run_command()

    def _on_command_key(self, _widget, event) -> bool:
        """Up/Down walk the command history; Tab completes a directory name."""
        if event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.complete_command()
            return True
        if event.keyval not in (Gdk.KEY_Up, Gdk.KEY_Down):
            return False
        if not self._command_history:
            return True
        if event.keyval == Gdk.KEY_Up:
            if self._history_index is None:
                self._history_index = len(self._command_history) - 1
            else:
                self._history_index = max(0, self._history_index - 1)
        else:
            if self._history_index is None:
                return True
            self._history_index += 1
            if self._history_index >= len(self._command_history):
                self._history_index = None
                self.command_entry.set_text("")
                return True
        self.command_entry.set_text(self._command_history[self._history_index])
        self.command_entry.set_position(-1)
        return True

    def complete_command(self) -> None:
        """Complete the directory name under the cursor.

        One match completes it and appends ``/`` so the next Tab descends; several
        matches extend to their common prefix, and when even that adds nothing the
        candidates are listed so the user can pick.
        """
        text = self.command_entry.get_text()
        prefix, base, fragment = split_for_completion(text)
        if prefix and not text.startswith(prefix):
            # Tab right after "cd" must at least add the separating space, even
            # when the candidates are ambiguous.
            self.command_entry.set_text(prefix)
            self.command_entry.set_position(-1)
        candidates = completion_candidates(base, fragment, self.directory)
        if not candidates:
            self._notify("没有匹配的目录", 3)
            return
        if len(candidates) == 1:
            self._set_completed(prefix, base, candidates[0] + "/")
            return
        common = _common_prefix(candidates)
        if len(common) > len(fragment):
            self._set_completed(prefix, base, common)
            return
        self._notify("  ".join(candidates[:12]) + ("…" if len(candidates) > 12 else ""), 6)

    def _set_completed(self, prefix: str, base: str, name: str) -> None:
        """Put a completed name back into the entry and park the cursor at the end."""
        text = prefix + base + name
        self.command_entry.set_text(text)
        self.command_entry.set_position(-1)

    def _tool_button(self, icon: str, tooltip: str, callback: Callable) -> Gtk.Button:
        button = Gtk.Button()
        button.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU))
        button.set_tooltip_text(tooltip)
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("vela-icon-button")
        button.connect("clicked", lambda *_: callback())
        return button

    # ------------------------------------------------------------------
    # theming
    # ------------------------------------------------------------------
    def apply_theme(self, theme: theme_mod.Theme) -> None:
        self.theme = theme
        self._icon_cache.clear()
        if self.directory:
            self.refresh()

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def set_directory(self, path: str, force: bool = False) -> bool:
        """Show ``path``; ignored unless following or ``force`` is set."""
        if not force and not self.following:
            return False
        return self._load(path)

    def navigate(self, path: str) -> bool:
        """User-initiated navigation: stops following the terminal."""
        if self.directory and os.path.abspath(path) != os.path.abspath(self.directory):
            self._previous_directory = self.directory
        self.following = False
        self._sync_toolbar()
        return self._load(path)

    def go_up(self) -> bool:
        parent = fsmodel.parent_path(self.directory or os.path.expanduser("~"))
        if not parent:
            self._notify("已经是根目录", 3)
            return False
        return self.navigate(parent)

    def go_home(self) -> bool:
        return self.navigate(os.path.expanduser("~"))

    def toggle_follow(self) -> None:
        self.following = not self.following
        self._sync_toolbar()
        notifier = getattr(self, "on_follow_changed", None)
        if notifier is not None:
            notifier()
        if self.following:
            self._notify("文件面板已跟随终端目录", 3)
            directory = self._terminal_directory()
            if directory:
                self._load(directory)
        else:
            self._notify("文件面板已停止跟随终端", 3)

    def toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.config.set("filebrowser.show_hidden", self.show_hidden)
        self._sync_toolbar()
        if self.directory:
            self.refresh()

    def refresh(self) -> bool:
        if not self.directory:
            return False
        return self._load(self.directory)

    def _terminal_directory(self) -> str:
        getter = getattr(self, "terminal_directory", None)
        if getter is None:
            return ""
        try:
            return getter() or ""
        except Exception:  # pragma: no cover - defensive
            return ""

    def _load(self, path: str) -> bool:
        listing = fsmodel.read_directory(
            path,
            show_hidden=self.show_hidden,
            max_entries=int(self.config.get("filebrowser.max_entries")),
            directories_first=bool(
                self.config.get("filebrowser.directories_first")
            ),
        )
        self.directory = listing.path or path
        self._fill(listing)
        return listing.ok

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def _fill(self, listing: fsmodel.Listing) -> None:
        self.store.clear()
        self._up_row = None
        if listing.ok:
            parent = fsmodel.parent_path(listing.path)
            if parent:
                icon = self._icon_for_directory(parent)
                self.store.append(
                    [
                        "..",
                        "",
                        "",
                        "",
                        icon,
                        parent,
                        True,
                        f"上级目录：{parent}",
                    ]
                )
                self._up_row = self.store.get_iter_first()
            for entry in listing.entries:
                icon = (
                    self._icon_for_directory(entry.path)
                    if entry.is_dir
                    else self._icon_for_file(entry)
                )
                tooltip = entry.path
                if entry.is_link and entry.link_target:
                    tooltip += f"\n链接到 {entry.link_target}"
                if not entry.readable:
                    tooltip += "\n（没有读取权限）"
                self.store.append(
                    [
                        entry.display_name,
                        entry.permissions,
                        entry.modified,
                        entry.size_text,
                        icon,
                        entry.path,
                        entry.is_dir,
                        tooltip,
                    ]
                )
        self._render_breadcrumbs(listing.path)
        summary = fsmodel.summarize(listing)
        self._summary.set_text(summary)
        self._summary.set_tooltip_text(listing.error or listing.path)
        if listing.error:
            self._summary.get_style_context().add_class("vela-error")
        else:
            self._summary.get_style_context().remove_class("vela-error")

    def _render_breadcrumbs(self, path: str) -> None:
        for child in list(self._breadcrumb.get_children()):
            self._breadcrumb.remove(child)
        if not path:
            self._breadcrumb.show_all()
            return
        crumbs = fsmodel.breadcrumbs(path)
        for index, (label, target) in enumerate(crumbs):
            if index:
                sep = Gtk.Label(label="/")
                sep.get_style_context().add_class("vela-dim")
                self._breadcrumb.pack_start(sep, False, False, 0)
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("vela-crumb")
            button.set_tooltip_text(target)
            # Later crumbs are more useful, so they get the space when the row
            # overflows; the first one keeps the root reachable.
            button.connect("clicked", lambda _b, t=target: self.navigate(t))
            self._breadcrumb.pack_start(button, index == len(crumbs) - 1, False, 0)
        self._breadcrumb.show_all()

    def _sync_toolbar(self) -> None:
        context = self._follow_button.get_style_context()
        if self.following:
            context.add_class("vela-active-toggle")
        else:
            context.remove_class("vela-active-toggle")
        hidden = self._hidden_button.get_style_context()
        if self.show_hidden:
            hidden.add_class("vela-active-toggle")
        else:
            hidden.remove_class("vela-active-toggle")
        self._up_button.set_sensitive(
            bool(self.directory) and bool(fsmodel.parent_path(self.directory))
        )

    # ------------------------------------------------------------------
    # icons
    # ------------------------------------------------------------------
    def _icon_size(self) -> int:
        return int(self.config.get("filebrowser.icon_size"))

    def _load_icon(self, icon, fallback: str) -> Optional[GdkPixbuf.Pixbuf]:
        theme = Gtk.IconTheme.get_default()
        if theme is None:
            return None
        size = self._icon_size()
        try:
            info = theme.lookup_by_gicon(icon, size, Gtk.IconLookupFlags.FORCE_SIZE)
            if info is not None:
                pixbuf = info.load_icon()
                if pixbuf is not None:
                    return pixbuf
        except GLib.Error:
            pass
        try:
            return theme.load_icon(fallback, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            return None

    def _icon_for_directory(self, path: str) -> Optional[GdkPixbuf.Pixbuf]:
        key = "dir"
        if key not in self._icon_cache:
            icon = Gio.ThemedIcon.new("folder")
            self._icon_cache[key] = self._load_icon(icon, "folder")
        return self._icon_cache[key]

    def _icon_for_file(self, entry: fsmodel.Entry) -> Optional[GdkPixbuf.Pixbuf]:
        content_type, _uncertain = Gio.content_type_guess(entry.path, None)
        key = content_type or "unknown"
        if key in self._icon_cache:
            return self._icon_cache[key]
        icon = Gio.content_type_get_icon(content_type) if content_type else None
        if icon is None:
            icon = Gio.ThemedIcon.new("text-x-generic")
        pixbuf = self._load_icon(icon, "text-x-generic")
        self._icon_cache[key] = pixbuf
        return pixbuf

    # ------------------------------------------------------------------
    # interaction
    # ------------------------------------------------------------------
    def selected_entry(self) -> Optional[fsmodel.Entry]:
        model, tree_iter = self.view.get_selection().get_selected()
        if tree_iter is None:
            return None
        path = model.get_value(tree_iter, COL_PATH)
        if not path:
            return None
        is_dir = bool(model.get_value(tree_iter, COL_IS_DIR))
        return fsmodel.Entry(
            name=os.path.basename(path.rstrip(os.sep)) or path,
            path=path,
            is_dir=is_dir,
        )

    def activate_selected(self) -> bool:
        model, tree_iter = self.view.get_selection().get_selected()
        if tree_iter is None:
            return False
        path = model.get_value(tree_iter, COL_PATH)
        is_dir = bool(model.get_value(tree_iter, COL_IS_DIR))
        if not path:
            return False
        if is_dir:
            return self.navigate(path)
        return self.open_path(path)

    def open_path(self, path: str) -> bool:
        """Open ``path`` the same way the terminal does."""
        if self._on_open is not None:
            return bool(self._on_open(path))
        target = files.classify(path)
        return files.open_with_desktop(target.path)

    def _on_row_activated(self, _view, row, _column) -> None:
        """Open the activated row.

        The ``row-activated`` signal delivers a ``Gtk.TreePath``; PyGObject's
        override of ``Gtk.TreeView.row_activated()`` takes a ``Gtk.TreeIter``.
        Both are accepted so a real double-click and a programmatic call behave
        the same (previously this assumed a TreeIter, so double-clicking a row
        raised inside the handler and did nothing).
        """
        tree_iter = self._as_iter(row)
        if tree_iter is None:
            return
        path = self.store.get_value(tree_iter, COL_PATH)
        if not path:
            return
        if self.store.get_value(tree_iter, COL_IS_DIR):
            self.navigate(path)
        else:
            self.open_path(path)

    def _as_iter(self, row):
        """Normalise a TreePath/TreeIter/None into a TreeIter (or ``None``)."""
        if row is None:
            return None
        if isinstance(row, Gtk.TreeIter):
            return row
        if isinstance(row, Gtk.TreePath):
            try:
                return self.store.get_iter(row)
            except (ValueError, TypeError):
                # A path that no longer exists (the list was refreshed between
                # the click and this handler) must not raise.
                return None
        # A model path string can also arrive from other callers.
        if isinstance(row, str):
            try:
                return self.store.get_iter_from_string(row)
            except (ValueError, TypeError):
                return None
        return None

    def _on_button_press(self, _widget, event) -> bool:
        if event.button != 3:
            return False
        path_info = self.view.get_path_at_pos(int(event.x), int(event.y))
        if path_info is not None:
            self.view.get_selection().select_path(path_info[0])
        self._show_context_menu(event)
        return True

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.activate_selected()
            return True
        if event.keyval == Gdk.KEY_BackSpace:
            self.go_up()
            return True
        if event.keyval == Gdk.KEY_F5:
            self.refresh()
            return True
        return False

    def _show_context_menu(self, event) -> None:
        entry = self.selected_entry()
        menu = Gtk.Menu()

        def add(label: str, callback: Callable, sensitive: bool = True) -> None:
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(sensitive)
            item.connect("activate", lambda *_: callback())
            item.show()
            menu.append(item)

        add("打开", self.activate_selected, entry is not None)
        if entry is not None and not entry.is_dir:
            add("用系统默认程序打开", lambda: self._open_external(entry.path))
        add("在文件管理器中显示", self._reveal_selected, entry is not None)
        add("复制路径", self._copy_selected_path, entry is not None)
        add("复制路径（转义）", self._copy_selected_quoted, entry is not None)
        menu.show_all()
        menu.popup_at_pointer(event)

    def _open_external(self, path: str) -> None:
        if files.open_with_desktop(path):
            self._notify("已用系统默认程序打开", 3)
        else:
            self._notify("找不到可用的系统默认程序", 6, "error")

    def _reveal_selected(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        if files.reveal_in_file_manager(entry.path):
            self._notify("已在文件管理器中显示", 3)
        else:
            self._notify("无法打开文件管理器", 6, "error")

    def _copy_selected_path(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(entry.path, -1)
        clipboard.store()
        self._notify("路径已复制", 3)

    def _copy_selected_quoted(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(files.shell_quote(entry.path), -1)
        clipboard.store()
        self._notify("转义后的路径已复制", 3)
