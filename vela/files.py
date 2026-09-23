"""Deciding how to open a path found in the terminal.

The rule is: open text and images inside Vela (that is the common case in a
terminal, and an in-process viewer is instant), hand directories and everything
else to the desktop's own handler, and never guess silently — the caller always
gets back what happened so it can be reported in the status bar.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

TEXT_EXTENSIONS = frozenset(
    """
    txt text log md markdown rst adoc org ini cfg conf toml yaml yml json jsonc
    xml html htm css scss sass less js jsx ts tsx mjs cjs vue svelte
    py pyi pyw rb php pl pm lua r sh bash zsh fish ksh csh
    c h cc cpp cxx hpp hh hxx m mm java kt kts scala go rs swift cs vb
    sql graphql gql proto thrift diff patch csv tsv env service desktop
    gitignore gitattributes dockerfile makefile cmake mk properties
    """.split()
)

IMAGE_EXTENSIONS = frozenset(
    "png jpg jpeg gif bmp webp svg ico tif tiff pnm ppm pgm pbm xpm".split()
)

TEXT_FILENAMES = frozenset(
    """
    README LICENSE LICENCE COPYING NOTICE CHANGELOG CHANGES AUTHORS TODO
    Makefile Dockerfile Vagrantfile Gemfile Rakefile Procfile
    """.split()
)

MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_DIRECTORY = "directory"
KIND_EXECUTABLE = "executable"
KIND_OTHER = "other"
KIND_MISSING = "missing"


@dataclass
class Target:
    """What a user-supplied path resolved to."""

    raw: str
    path: str
    kind: str = KIND_OTHER
    exists: bool = False
    readable: bool = False
    size: int = 0
    reason: str = ""

    @property
    def openable_internally(self) -> bool:
        return self.kind in (KIND_TEXT, KIND_IMAGE) and self.readable


def is_probably_text(path: str) -> bool:
    """Extension-based guess; unknown extensions fall back to a byte sniff."""
    name = os.path.basename(path)
    lowered = name.lower()
    extension = lowered.rsplit(".", 1)[-1] if "." in lowered else ""
    if extension in TEXT_EXTENSIONS:
        return True
    if name in TEXT_FILENAMES or lowered in {n.lower() for n in TEXT_FILENAMES}:
        return True
    if extension and extension not in TEXT_EXTENSIONS:
        return False
    return looks_like_text(path)


def looks_like_text(path: str, sample: int = 4096) -> bool:
    """Sniff the first bytes: no NUL and decodable, mostly printable text."""
    try:
        with open(path, "rb") as handle:
            chunk = handle.read(sample)
    except OSError:
        return False
    if not chunk:
        return True
    if b"\x00" in chunk:
        return False
    decoded = None
    try:
        decoded = chunk.decode("utf-8")
    except UnicodeDecodeError:
        # The sample may cut a multi-byte character in half; drop up to three
        # trailing bytes before giving up on the file.
        decoded = None
        for trim in range(1, 4):
            try:
                decoded = chunk[:-trim].decode("utf-8")
                break
            except UnicodeDecodeError:
                continue
        if decoded is None:
            return False
    # Count decoded characters, not bytes: otherwise every CJK or accented text
    # file looks like binary noise because its bytes are all >= 0x80.
    printable = sum(
        1
        for character in decoded
        if character in "\t\n\r" or character.isprintable()
    )
    return printable / max(1, len(decoded)) > 0.85


def classify(path: str, follow_symlinks: bool = True) -> Target:
    """Resolve ``path`` (``~`` and relative paths allowed) and classify it."""
    raw = path
    if not path:
        return Target(raw=raw, path="", kind=KIND_MISSING, reason="路径为空")
    expanded = os.path.expandvars(os.path.expanduser(path.strip().strip("'\"")))
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    target = Target(raw=raw, path=expanded)
    try:
        info = os.stat(expanded) if follow_symlinks else os.lstat(expanded)
    except FileNotFoundError:
        target.kind = KIND_MISSING
        target.reason = "路径不存在"
        return target
    except PermissionError:
        target.kind = KIND_OTHER
        target.reason = "没有访问权限"
        return target
    except OSError as error:
        target.kind = KIND_OTHER
        target.reason = error.strerror or str(error)
        return target

    target.exists = True
    target.readable = os.access(expanded, os.R_OK)
    target.size = info.st_size
    if os.path.isdir(expanded):
        target.kind = KIND_DIRECTORY
        return target
    extension = expanded.rsplit(".", 1)[-1].lower() if "." in expanded else ""
    if extension in IMAGE_EXTENSIONS:
        target.kind = KIND_IMAGE
        if target.size > MAX_IMAGE_BYTES:
            target.kind = KIND_OTHER
            target.reason = "图片过大，改用系统默认程序打开"
        return target
    if is_probably_text(expanded):
        target.kind = KIND_TEXT
        if target.size > MAX_TEXT_BYTES:
            target.kind = KIND_OTHER
            target.reason = "文件过大，改用系统默认程序打开"
        return target
    if os.access(expanded, os.X_OK):
        target.kind = KIND_EXECUTABLE
        return target
    target.kind = KIND_OTHER
    return target


def open_with_desktop(path: str) -> bool:
    """Hand ``path`` to the desktop.  Returns whether a handler was launched."""
    for command in _desktop_commands(path):
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            continue
    return False


def _desktop_commands(path: str) -> List[List[str]]:
    commands: List[List[str]] = [["xdg-open", path]]
    if os.path.isdir(path):
        commands.append(["nautilus", path])
    commands.append(["gio", "open", path])
    return commands


def reveal_in_file_manager(path: str) -> bool:
    """Open the containing directory, selecting ``path`` when possible."""
    directory = path if os.path.isdir(path) else os.path.dirname(path) or "."
    if shutil.which("nautilus") is not None:
        try:
            subprocess.Popen(
                ["nautilus", "--select", path] if not os.path.isdir(path) else ["nautilus", directory],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except OSError:
            pass
    return open_with_desktop(directory)


def read_text(path: str, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Read a text file, refusing anything larger than ``max_bytes``."""
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ValueError(
            f"文件过大（{size} 字节），超过 {max_bytes} 字节上限"
        )
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def write_text(path: str, text: str) -> None:
    """Write ``text`` atomically so a crash cannot truncate the original."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temporary = os.path.join(directory, f".{os.path.basename(path)}.vela-tmp{os.getpid()}")
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = None
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
        if mode is not None:
            # os.replace() would otherwise give the file the process umask.
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def shell_quote(path: str) -> str:
    """Quote ``path`` for pasting into a shell."""
    if not path:
        return "''"
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@%_+=:,./-")
    if all(character in safe for character in path):
        return path
    return "'" + path.replace("'", "'\\''") + "'"


def candidate_paths(text: str) -> List[str]:
    """Extract plausible filesystem paths from terminal output.

    Used when the terminal view has no explicit path match under the cursor.
    """
    found: List[str] = []
    for token in _split_tokens(text):
        cleaned = token.strip("()[]{}<>,;\"'`")
        if not cleaned or len(cleaned) > 4096:
            continue
        if "://" in cleaned:
            continue
        if not any(character in cleaned for character in "./~"):
            continue
        expanded = os.path.expanduser(os.path.expandvars(cleaned))
        if os.path.exists(expanded):
            # Return absolute paths so callers never depend on the cwd that was
            # current when the output was produced.
            found.append(os.path.abspath(expanded))
    return found


def _split_tokens(text: str) -> Iterable[str]:
    for line in text.splitlines():
        yield from line.replace("\t", " ").split(" ")
