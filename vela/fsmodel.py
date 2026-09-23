"""Directory reading and formatting for the file browser panel.

Kept free of GTK so the interesting parts (sorting, permission strings, time
formatting, navigation and the error cases) can be tested against a temporary
directory without a display.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

#: Entries beyond this are dropped so a huge directory cannot freeze the UI.
DEFAULT_MAX_ENTRIES = 5000

#: Width used for the size column; kept in bytes and formatted on demand.
_UNITS = ("B", "K", "M", "G", "T", "P")


@dataclass
class Entry:
    """One row in the browser."""

    name: str
    path: str
    is_dir: bool = False
    is_link: bool = False
    link_target: str = ""
    size: int = 0
    mtime: float = 0.0
    mode: int = 0
    readable: bool = True

    @property
    def permissions(self) -> str:
        return format_mode(self.mode, self.is_dir)

    @property
    def modified(self) -> str:
        return format_time(self.mtime)

    @property
    def display_name(self) -> str:
        if self.is_link and self.link_target:
            return f"{self.name} → {self.link_target}"
        return self.name

    @property
    def size_text(self) -> str:
        if self.is_dir:
            return ""
        return format_size(self.size)


@dataclass
class Listing:
    """Result of reading a directory."""

    path: str
    entries: List[Entry]
    error: str = ""
    truncated: int = 0
    show_hidden: bool = False

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def total(self) -> int:
        return len(self.entries) + self.truncated


def format_mode(mode: int, is_dir: bool = False) -> str:
    """``st_mode`` -> ``drwxr-xr-x`` style string.

    Handles the setuid/setgid/sticky bits the way ``ls`` does: the execute slot
    becomes ``s``/``t`` when the corresponding special bit is set, and the
    uppercase variants when it is set without execute permission.
    """
    kind = "d" if is_dir else "-"
    if stat.S_ISLNK(mode):
        kind = "l"
    elif stat.S_ISSOCK(mode):
        kind = "s"
    elif stat.S_ISFIFO(mode):
        kind = "p"
    elif stat.S_ISCHR(mode):
        kind = "c"
    elif stat.S_ISBLK(mode):
        kind = "b"

    bits = ""
    triplets = (
        (stat.S_IRUSR, stat.S_IWUSR, stat.S_IXUSR),
        (stat.S_IRGRP, stat.S_IWGRP, stat.S_IXGRP),
        (stat.S_IROTH, stat.S_IWOTH, stat.S_IXOTH),
    )
    for read_bit, write_bit, exec_bit in triplets:
        bits += "r" if mode & read_bit else "-"
        bits += "w" if mode & write_bit else "-"
        bits += "x" if mode & exec_bit else "-"

    chars = list(bits)
    if mode & stat.S_ISUID:
        chars[2] = "s" if mode & stat.S_IXUSR else "S"
    if mode & stat.S_ISGID:
        chars[5] = "s" if mode & stat.S_IXGRP else "S"
    if mode & stat.S_ISVTX:
        chars[8] = "t" if mode & stat.S_IXOTH else "T"
    return kind + "".join(chars)


def format_time(timestamp: float, now: Optional[float] = None) -> str:
    """Timestamp -> ``Sep 22 18:16``, or a year for older entries."""
    if timestamp <= 0:
        return ""
    now = time.time() if now is None else now
    try:
        moment = time.localtime(timestamp)
    except (OSError, ValueError, OverflowError):
        return ""
    # Match "ls -l": recent files show the time, older ones show the year.
    if abs(now - timestamp) > 180 * 24 * 3600:
        return time.strftime("%b %e  %Y", moment)
    return time.strftime("%b %e %H:%M", moment)


def format_size(size: int) -> str:
    """Bytes -> ``1.2M`` style short size."""
    if size < 0:
        return ""
    value = float(size)
    index = 0
    while value >= 1024 and index < len(_UNITS) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)}{_UNITS[index]}"
    if value < 10:
        return f"{value:.1f}{_UNITS[index]}"
    return f"{int(round(value))}{_UNITS[index]}"


def parent_path(path: str) -> str:
    """Parent of ``path``, or ``""`` when already at the filesystem root."""
    normalized = os.path.abspath(os.path.expanduser(path))
    if normalized == os.path.sep:
        return ""
    parent = os.path.dirname(normalized)
    return parent or os.path.sep


def breadcrumbs(path: str) -> List[Tuple[str, str]]:
    """``(label, full_path)`` for each path segment, root first.

    The first item is always ``/`` so the user can jump to the root; the user's
    home directory is shown as ``~``.
    """
    normalized = os.path.abspath(os.path.expanduser(path))
    home = os.path.expanduser("~")
    crumbs: List[Tuple[str, str]] = [("/", "/")]
    if normalized == os.path.sep:
        return crumbs
    current = os.path.sep
    for part in normalized.strip(os.path.sep).split(os.path.sep):
        current = os.path.join(current, part)
        label = part
        if current == home:
            label = "~"
        crumbs.append((label, current))
    return crumbs


def natural_key(name: str) -> Tuple:
    """Sort key giving ``file2`` before ``file10`` (case-insensitive)."""
    parts: List[Tuple[int, object]] = []
    digits = ""
    for char in name.lower():
        if char.isdigit():
            digits += char
            continue
        if digits:
            parts.append((0, int(digits)))
            digits = ""
        parts.append((1, char))
    if digits:
        parts.append((0, int(digits)))
    return tuple(parts)


def sort_entries(entries: Sequence[Entry], directories_first: bool = True) -> List[Entry]:
    """Directories first (optional), then natural name order."""
    if not directories_first:
        return sorted(entries, key=lambda item: natural_key(item.name))
    return sorted(entries, key=lambda item: (not item.is_dir, natural_key(item.name)))


def read_directory(
    path: str,
    show_hidden: bool = False,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    directories_first: bool = True,
    follow_links: bool = True,
) -> Listing:
    """Read ``path`` into a :class:`Listing`; never raises for normal errors."""
    expanded = os.path.abspath(os.path.expanduser(path)) if path else ""
    if not expanded:
        return Listing(path="", entries=[], error="路径为空", show_hidden=show_hidden)
    if not os.path.exists(expanded):
        return Listing(
            path=expanded, entries=[], error="目录不存在", show_hidden=show_hidden
        )
    if not os.path.isdir(expanded):
        return Listing(
            path=expanded, entries=[], error="不是目录", show_hidden=show_hidden
        )
    try:
        names = os.listdir(expanded)
    except PermissionError:
        return Listing(
            path=expanded, entries=[], error="没有访问权限", show_hidden=show_hidden
        )
    except OSError as error:
        return Listing(
            path=expanded,
            entries=[],
            error=error.strerror or str(error),
            show_hidden=show_hidden,
        )

    if not show_hidden:
        names = [name for name in names if not name.startswith(".")]

    entries: List[Entry] = []
    for name in names:
        full = os.path.join(expanded, name)
        try:
            info = os.stat(full) if follow_links else os.lstat(full)
        except OSError:
            # A broken symlink or a file that vanished mid-listing: keep the
            # name so the user still sees it, but without details.
            entries.append(
                Entry(name=name, path=full, mode=0, readable=False)
            )
            continue
        is_link = os.path.islink(full)
        entry = Entry(
            name=name,
            path=full,
            is_dir=stat.S_ISDIR(info.st_mode),
            is_link=is_link,
            size=info.st_size,
            mtime=info.st_mtime,
            mode=info.st_mode,
            readable=os.access(full, os.R_OK),
        )
        if is_link:
            try:
                entry.link_target = os.readlink(full)
            except OSError:
                entry.link_target = ""
        entries.append(entry)

    ordered = sort_entries(entries, directories_first)
    truncated = 0
    if max_entries > 0 and len(ordered) > max_entries:
        truncated = len(ordered) - max_entries
        ordered = ordered[:max_entries]
    return Listing(
        path=expanded,
        entries=ordered,
        truncated=truncated,
        show_hidden=show_hidden,
    )


def resolve_entry_target(entry: Entry) -> str:
    """Where double-clicking ``entry`` should lead."""
    if not entry.is_link or not entry.link_target:
        return entry.path
    target = entry.link_target
    if not os.path.isabs(target):
        target = os.path.join(os.path.dirname(entry.path), target)
    return os.path.normpath(target)


def summarize(listing: Listing) -> str:
    """One-line description for the panel footer."""
    if listing.error:
        return listing.error
    folders = sum(1 for entry in listing.entries if entry.is_dir)
    files = len(listing.entries) - folders
    text = f"{folders} 个文件夹 · {files} 个文件"
    if listing.truncated:
        text += f"（另有 {listing.truncated} 项未显示）"
    return text
