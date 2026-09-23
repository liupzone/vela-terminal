"""Font discovery and installation.

Two jobs:

* list the monospace fonts that are actually installed, so the preferences
  dialog can offer real choices instead of a text field that accepts typos;
* download and install popular programming fonts on request.

The download path is deliberately explicit (never automatic) and validates what
it fetched: a failed request easily returns an HTML error page, and installing
that as a "font" would silently break text rendering.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

# Magic numbers for the font containers we accept.
_SFNT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf", b"wOFF", b"wOF2")

# Extensions worth extracting from a downloaded archive.
_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")

FONT_DIR_ENV = "VELA_FONT_DIR"


def font_dir() -> str:
    """Where downloaded fonts are installed (overridable for tests)."""
    override = os.environ.get(FONT_DIR_ENV)
    if override:
        return override
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(data_home, "fonts")


@dataclass
class FontCandidate:
    """A downloadable font package."""

    key: str
    name: str
    description: str
    url: str
    licence: str = ""


# Fonts offered in the preferences dialog: open-licensed, with stable release
# archives.
CATALOG: Tuple[FontCandidate, ...] = (
    FontCandidate(
        key="jetbrains-mono",
        name="JetBrains Mono",
        description="为长时间阅读代码设计，字形清晰",
        url=(
            "https://github.com/JetBrains/JetBrainsMono/releases/download/"
            "v2.304/JetBrainsMono-2.304.zip"
        ),
        licence="OFL-1.1",
    ),
    FontCandidate(
        key="fira-code",
        name="Fira Code",
        description="Mozilla 出品，编程连字最丰富的字体之一",
        url=(
            "https://github.com/tonsky/FiraCode/releases/download/6.2/"
            "Fira_Code_v6.2.zip"
        ),
        licence="OFL-1.1",
    ),
    FontCandidate(
        key="hack",
        name="Hack",
        description="专为源码设计，易区分 0/O、1/l",
        url=(
            "https://github.com/source-foundry/Hack/releases/download/"
            "v3.003/Hack-v3.003-ttf.zip"
        ),
        licence="MIT",
    ),
    FontCandidate(
        key="cascadia-code",
        name="Cascadia Code",
        description="微软终端字体，带连字与电力线字形",
        url=(
            "https://github.com/microsoft/cascadia-code/releases/download/"
            "v2111.01/CascadiaCode-2111.01.zip"
        ),
        licence="OFL-1.1",
    ),
    FontCandidate(
        key="source-code-pro",
        name="Source Code Pro",
        description="Adobe 出品，中性耐看，适合中英混排",
        url=(
            "https://github.com/adobe-fonts/source-code-pro/releases/download/"
            "2.038R-ro%2F1.058R-it%2F1.018R-VAR/"
            "source-code-pro-2.038R-ro-1.058R-it.zip"
        ),
        licence="OFL-1.1",
    ),
    FontCandidate(
        key="nerd-symbols",
        name="Nerd Fonts 图标字形",
        description="图标与电力线字形，可配合任意等宽字体使用",
        url=(
            "https://github.com/ryanoasis/nerd-fonts/releases/download/"
            "v3.5.1/NerdFontsSymbolsOnly.zip"
        ),
        licence="MIT",
    ),
)


def catalog() -> Tuple[FontCandidate, ...]:
    return CATALOG


def find_candidate(key: str) -> Optional[FontCandidate]:
    for entry in CATALOG:
        if entry.key == key:
            return entry
    return None


def monospace_families() -> List[str]:
    """Names of installed monospace families, sorted.

    Uses Pango's own monospace flag rather than guessing from the name, which
    would both miss fonts called "Code" and wrongly include proportional ones.
    """
    try:
        import gi

        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo

        families = PangoCairo.FontMap.get_default().list_families()
    except Exception:  # pragma: no cover - no GTK/Pango available
        return []
    names = [family.get_name() for family in families if family.is_monospace()]
    return sorted(set(names), key=lambda name: name.lower())


def is_installed(family: str) -> bool:
    return family in set(monospace_families())


def installed_catalog_keys() -> List[str]:
    """Catalog entries whose font family is already available."""
    available = {name.lower() for name in monospace_families()}
    found: List[str] = []
    for entry in CATALOG:
        needle = entry.name.lower()
        if any(needle in name or name in needle for name in available):
            found.append(entry.key)
    return found


def split_font_list(value: str) -> List[str]:
    """Split a font fallback list (``"JetBrains Mono, Noto Sans Mono CJK SC"``)."""
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def join_font_list(families: Sequence[str]) -> str:
    return ", ".join(part for part in families if part)


def looks_like_font(data: bytes) -> bool:
    """Whether ``data`` starts with a known font container magic."""
    return any(data.startswith(magic) for magic in _SFNT_MAGICS)


def font_files_in_archive(path: str) -> List[str]:
    """Names of font files inside a zip, skipping docs and metadata."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(_FONT_EXTENSIONS) and not name.endswith("/")
            ]
            # Prefer static files over variable ones when both are shipped: the
            # variable files can confuse older font stacks.
            static = [name for name in names if "/variable/" not in name.lower()]
            chosen = static or names
            valid: List[str] = []
            for name in chosen:
                try:
                    head = archive.read(name)[:4]
                except (KeyError, zipfile.BadZipFile):
                    continue
                if looks_like_font(head):
                    valid.append(name)
            return valid
    except (zipfile.BadZipFile, OSError):
        return []


class DownloadError(RuntimeError):
    """Raised when a font could not be fetched or installed."""


def download(url: str, timeout: float = 120.0, opener: Optional[Callable] = None) -> bytes:
    """Fetch ``url`` into memory, reporting a useful error on failure."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "Vela-Terminal/1.0 (+font installer)"}
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        raise DownloadError(f"服务器返回 {error.code}") from error
    except urllib.error.URLError as error:
        raise DownloadError(f"网络不可用：{error.reason}") from error
    except OSError as error:
        raise DownloadError(f"下载失败：{error}") from error
    if not data:
        raise DownloadError("下载内容为空")
    return data


def install_archive(data: bytes, key: str, directory: Optional[str] = None) -> List[str]:
    """Extract the fonts in ``data`` into ``directory``; return installed paths.

    Only files that pass the magic check are written, and each package gets its
    own subdirectory so re-installing replaces rather than accumulates.
    """
    target_root = directory or font_dir()
    package_dir = os.path.join(target_root, key)
    if not data:
        raise DownloadError("压缩包为空")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
        handle.write(data)
        temp_path = handle.name
    try:
        names = font_files_in_archive(temp_path)
        if not names:
            raise DownloadError("压缩包里没有可用的字体文件")
        # Replace the package directory rather than adding to it: a newer release
        # may drop or rename files, and stale ones would stay selectable forever.
        if os.path.isdir(package_dir):
            shutil.rmtree(package_dir, ignore_errors=True)
        os.makedirs(package_dir, exist_ok=True)
        installed: List[str] = []
        with zipfile.ZipFile(temp_path) as archive:
            for name in names:
                # Flatten the archive layout; fontconfig does not need it.
                destination = os.path.join(package_dir, os.path.basename(name))
                with archive.open(name) as source, open(destination, "wb") as out:
                    shutil.copyfileobj(source, out)
                installed.append(destination)
        return installed
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def refresh_font_cache() -> bool:
    """Tell fontconfig about newly installed fonts."""
    if shutil.which("fc-cache") is None:
        return False
    try:
        subprocess.run(
            ["fc-cache", "-f", font_dir()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        return True
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        return False


def install(key: str, directory: Optional[str] = None, timeout: float = 120.0) -> List[str]:
    """Download and install one catalog entry; returns the installed paths."""
    entry = find_candidate(key)
    if entry is None:
        raise DownloadError(f"未知字体：{key}")
    data = download(entry.url, timeout=timeout)
    installed = install_archive(data, entry.key, directory)
    refresh_font_cache()
    return installed


def installed_font_files(directory: Optional[str] = None) -> List[str]:
    """Every font file under the font directory (for the preferences summary)."""
    root = directory or font_dir()
    found: List[str] = []
    if not os.path.isdir(root):
        return found
    for current, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(_FONT_EXTENSIONS):
                found.append(os.path.join(current, name))
    return sorted(found)
