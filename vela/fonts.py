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
import glob
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

#: Where Windows keeps its fonts, relative to a mount point.
WINDOWS_FONT_SUBDIR = os.path.join("Windows", "Fonts")

#: Extra locations to probe, overriding the glob search (used by tests).
WINDOWS_FONT_DIR_ENV = "VELA_WINDOWS_FONT_DIR"


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


def cjk_families() -> List[str]:
    """Installed families that can render Chinese, sorted.

    Used for the fallback-font picker: the fallback is typically proportional
    (微软雅黑, Noto Sans CJK) so it never appears in the monospace list.
    """
    try:
        import gi

        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo

        families = PangoCairo.FontMap.get_default().list_families()
    except Exception:  # pragma: no cover - Pango unavailable
        return []
    names: List[str] = []
    for family in families:
        name = family.get_name()
        if _family_looks_cjk(name):
            names.append(name)
    return sorted(set(names), key=lambda value: value.lower())


#: Name fragments of families that carry CJK coverage.  Pango exposes no direct
#: "has Chinese glyphs" flag, and shaping every family on the system is slow, so
#: the well-known CJK families are matched by name.
_CJK_NAME_HINTS = (
    "cjk", "yahei", "雅黑", "simsun", "simhei", "songti", "heiti", "kaiti",
    "fangsong", "dengxian", "noto sans sc", "noto serif sc", "source han",
    "wenquanyi", "micro hei", "zen hei", "droid sans fallback", "uming", "ukai",
    "arphic", "wqy", "pingfang", "hiragino", "heiti sc", "microsoft jhenghei",
    "sarasa", "maple", "lxgw", "hanserif", "hansans",
)


def _family_looks_cjk(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _CJK_NAME_HINTS)


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


# ---------------------------------------------------------------------------
# fonts that already exist on this machine
# ---------------------------------------------------------------------------
@dataclass
class LocalFont:
    """A font file found on the machine that can be imported."""

    key: str
    name: str
    description: str
    #: Glob patterns, relative to a Windows mount, that locate the files.
    patterns: Tuple[str, ...]

    def resolve(self, root: str) -> List[str]:
        """Absolute paths of the files for this font under ``root``."""
        found: List[str] = []
        for pattern in self.patterns:
            found.extend(glob.glob(os.path.join(root, pattern)))
        return sorted(set(found))


#: Fonts shipped with Windows.  Vela never downloads these: they are
#: proprietary, and the only legitimate copy is the one already on the user's
#: machine (a mounted Windows partition).  Importing makes them available to
#: Vela without touching the original files.
LOCAL_CATALOG: Tuple[LocalFont, ...] = (
    LocalFont(
        key="microsoft-yahei",
        name="微软雅黑",
        description="Windows 中文界面字体，屏幕显示清晰（常规/粗体/细体）",
        patterns=("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"),
    ),
    LocalFont(
        key="microsoft-dengxian",
        name="等线",
        description="Windows 10 起的中文正文字体，笔画较细",
        patterns=("Deng.ttf", "Dengb.ttf", "Dengl.ttf"),
    ),
    LocalFont(
        key="simhei",
        name="黑体",
        description="Windows 经典中文黑体",
        patterns=("simhei.ttf",),
    ),
    LocalFont(
        key="simsun",
        name="宋体",
        description="Windows 经典中文宋体（含 NSimSun）",
        patterns=("simsun.ttc",),
    ),
    LocalFont(
        key="kaiti",
        name="楷体",
        description="Windows 中文楷体",
        patterns=("STKAITI.TTF", "simkai.ttf"),
    ),
)


def local_catalog() -> Tuple[LocalFont, ...]:
    return LOCAL_CATALOG


def find_local(key: str) -> Optional[LocalFont]:
    for entry in LOCAL_CATALOG:
        if entry.key == key:
            return entry
    return None


def windows_font_dirs() -> List[str]:
    """Directories holding a Windows font collection, if any.

    Looks at every mounted filesystem for a ``Windows/Fonts`` directory.  The
    environment override lets tests point at a fixture instead of a real mount.
    """
    override = os.environ.get(WINDOWS_FONT_DIR_ENV)
    if override:
        return [override] if os.path.isdir(override) else []
    roots: List[str] = []
    # Typical mount points: /media/<user>/<label>, /mnt/<label>, /run/media/...
    for base in ("/media/*/*", "/mnt/*", "/run/media/*/*", "/media/*"):
        for candidate in glob.glob(base):
            fonts = os.path.join(candidate, WINDOWS_FONT_SUBDIR)
            if os.path.isdir(fonts):
                roots.append(fonts)
    return sorted(set(roots))


def available_local_fonts() -> List[Tuple[LocalFont, List[str]]]:
    """Local catalog entries that exist on this machine, with their files."""
    found: List[Tuple[LocalFont, List[str]]] = []
    for root in windows_font_dirs():
        for entry in LOCAL_CATALOG:
            paths = entry.resolve(root)
            if paths:
                found.append((entry, paths))
    return found


def is_local_installed(key: str) -> bool:
    """Whether a local font has already been imported into Vela's font dir."""
    entry = find_local(key)
    if entry is None:
        return False
    package_dir = os.path.join(font_dir(), entry.key)
    return os.path.isdir(package_dir) and bool(
        [
            name
            for name in os.listdir(package_dir)
            if name.lower().endswith(_FONT_EXTENSIONS)
        ]
    )


def import_local(key: str, directory: Optional[str] = None) -> List[str]:
    """Copy a machine-local font into Vela's font directory.

    The source files are only read; they are never modified or removed.  Only
    files that pass the font magic check are copied, and the package directory
    is replaced so a re-import cannot accumulate stale files.
    """
    entry = find_local(key)
    if entry is None:
        raise DownloadError(f"未知字体：{key}")
    sources: List[str] = []
    for root in windows_font_dirs():
        sources.extend(entry.resolve(root))
    if not sources:
        raise DownloadError(
            "没有找到可导入的字体文件；请确认 Windows 分区已挂载"
        )
    target_root = directory or font_dir()
    package_dir = os.path.join(target_root, entry.key)
    # Validate before touching the target so a bad source leaves nothing behind.
    valid: List[str] = []
    for source in sources:
        try:
            with open(source, "rb") as handle:
                head = handle.read(4)
        except OSError as error:
            raise DownloadError(f"无法读取 {os.path.basename(source)}：{error}")
        if not looks_like_font(head):
            raise DownloadError(
                f"{os.path.basename(source)} 不是有效的字体文件"
            )
        valid.append(source)
    if not valid:
        raise DownloadError("没有可导入的字体文件")
    if os.path.isdir(package_dir):
        shutil.rmtree(package_dir, ignore_errors=True)
    os.makedirs(package_dir, exist_ok=True)
    imported: List[str] = []
    for source in valid:
        destination = os.path.join(package_dir, os.path.basename(source))
        try:
            shutil.copy2(source, destination)
        except OSError as error:
            raise DownloadError(
                f"复制 {os.path.basename(source)} 失败：{error}"
            )
        imported.append(destination)
    refresh_font_cache()
    return imported
