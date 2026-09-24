"""Saving and restoring pane layouts.

The pane tree is converted to plain dictionaries here, with no GTK dependency,
so serialisation, version handling and the recovery from damaged data can all be
tested directly.

What is stored: the tree shape (split direction and divider position), the kind
of each pane, and the working directory of each terminal.  What is *not* stored:
terminal output.  A scrollback buffer is megabytes of text that would bloat the
file and slow startup, and restoring a clean shell in the right directory is
what the user actually wants.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: Bumped when the on-disk shape changes; readers accept anything <= this.
LAYOUT_VERSION = 1

#: Kinds a pane may have in a saved layout.
VALID_KINDS = ("terminal", "sysinfo", "filebrowser")

#: Fallback when a saved directory no longer exists.
_FALLBACK_KIND = "terminal"


class LayoutError(ValueError):
    """Raised when a layout cannot be parsed at all."""


@dataclass
class PaneSpec:
    """One leaf of a saved layout."""

    kind: str = "terminal"
    cwd: str = ""
    title: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"type": "pane", "kind": self.kind}
        if self.cwd:
            data["cwd"] = self.cwd
        if self.title:
            data["title"] = self.title
        return data


@dataclass
class SplitSpec:
    """An internal node: two children side by side or stacked."""

    orientation: str = "h"  # "h" = left/right, "v" = top/bottom
    position: int = 0
    first: Any = None
    second: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "split",
            "orientation": self.orientation,
            "position": int(self.position),
            "first": to_dict(self.first),
            "second": to_dict(self.second),
        }


@dataclass
class TabSpec:
    """One tab: a title and a pane tree."""

    title: str = ""
    tree: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "tree": to_dict(self.tree)}


@dataclass
class LayoutSpec:
    """A whole window: its tabs, in order."""

    name: str = ""
    tabs: List[TabSpec] = field(default_factory=list)
    version: int = LAYOUT_VERSION
    saved_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "saved_at": self.saved_at or time.time(),
            "tabs": [tab.to_dict() for tab in self.tabs],
        }

    @property
    def pane_count(self) -> int:
        return sum(count_panes(tab.tree) for tab in self.tabs)


# ---------------------------------------------------------------------------
# serialisation
# ---------------------------------------------------------------------------
def to_dict(node: Any) -> Dict[str, Any]:
    """Convert a ``PaneSpec``/``SplitSpec`` (or a raw dict) to a dict."""
    if node is None:
        return PaneSpec().to_dict()
    if isinstance(node, dict):
        return dict(node)
    converter = getattr(node, "to_dict", None)
    if converter is None:
        raise LayoutError(f"无法序列化的节点：{type(node).__name__}")
    return converter()


def from_dict(data: Any, depth: int = 0) -> Any:
    """Rebuild a spec tree, repairing anything unusable.

    Damaged input is not fatal: an unknown pane kind becomes a terminal, a
    missing child becomes an empty terminal, and excessive nesting is truncated
    (a runaway tree would otherwise hang the UI).
    """
    if depth > 32:
        raise LayoutError("布局嵌套过深，可能是文件损坏")
    if not isinstance(data, dict):
        return PaneSpec()
    kind = data.get("type")
    if kind == "split":
        orientation = data.get("orientation")
        if orientation not in ("h", "v"):
            orientation = "h"
        return SplitSpec(
            orientation=orientation,
            position=_as_int(data.get("position"), 0),
            first=from_dict(data.get("first"), depth + 1),
            second=from_dict(data.get("second"), depth + 1),
        )
    # Everything else is treated as a pane.
    pane_kind = data.get("kind")
    if pane_kind not in VALID_KINDS:
        pane_kind = _FALLBACK_KIND
    return PaneSpec(
        kind=pane_kind,
        cwd=str(data.get("cwd") or ""),
        title=str(data.get("title") or ""),
    )


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def count_panes(node: Any) -> int:
    """Number of leaves in a spec tree."""
    if isinstance(node, SplitSpec):
        return count_panes(node.first) + count_panes(node.second)
    return 1


def iter_panes(node: Any):
    """Yield every ``PaneSpec`` in a spec tree, left to right."""
    if isinstance(node, SplitSpec):
        yield from iter_panes(node.first)
        yield from iter_panes(node.second)
    else:
        yield node


def prune_missing_directories(spec: LayoutSpec) -> List[str]:
    """Drop directories that no longer exist; returns the ones that were changed.

    A layout saved weeks ago may point at a deleted project.  Restoring should
    still work, just from the home directory, and the user should be told which
    panes moved.
    """
    changed: List[str] = []
    for tab in spec.tabs:
        for pane in iter_panes(tab.tree):
            if pane.cwd and not os.path.isdir(pane.cwd):
                changed.append(pane.cwd)
                pane.cwd = ""
    return changed


def normalize(spec: LayoutSpec) -> LayoutSpec:
    """Fill in defaults so a spec is safe to apply."""
    if not spec.tabs:
        spec.tabs = [TabSpec(tree=PaneSpec())]
    for tab in spec.tabs:
        if tab.tree is None:
            tab.tree = PaneSpec()
        if not isinstance(tab.title, str):
            tab.title = str(tab.title or "")
    spec.version = min(_as_int(spec.version, LAYOUT_VERSION), LAYOUT_VERSION)
    return spec


# ---------------------------------------------------------------------------
# named layouts
# ---------------------------------------------------------------------------
def named_layouts(config) -> Dict[str, LayoutSpec]:
    """Named layouts from the config, skipping anything unreadable."""
    result: Dict[str, LayoutSpec] = {}
    section = config.section("layouts")
    for name, payload in section.items():
        spec = parse_layout(payload, name=name)
        if spec is not None:
            result[name] = spec
    return result


def parse_layout(payload: Any, name: str = "") -> Optional[LayoutSpec]:
    """Build a :class:`LayoutSpec` from stored data; ``None`` when unusable."""
    if not isinstance(payload, dict):
        return None
    tabs_data = payload.get("tabs")
    if not isinstance(tabs_data, list) or not tabs_data:
        return None
    tabs: List[TabSpec] = []
    for entry in tabs_data:
        if not isinstance(entry, dict):
            continue
        tabs.append(
            TabSpec(
                title=str(entry.get("title") or ""),
                tree=from_dict(entry.get("tree")),
            )
        )
    if not tabs:
        return None
    return normalize(
        LayoutSpec(
            name=str(payload.get("name") or name),
            tabs=tabs,
            version=_as_int(payload.get("version"), LAYOUT_VERSION),
            saved_at=float(payload.get("saved_at") or 0.0),
        )
    )


def store_named_layout(config, spec: LayoutSpec, name: Optional[str] = None) -> str:
    """Save ``spec`` under ``name`` in the config; returns the name used."""
    key = (name or spec.name or "").strip()
    if not key:
        raise LayoutError("布局名称不能为空")
    spec.name = key
    section = config.section("layouts")
    section[key] = spec.to_dict()
    return key


def remove_named_layout(config, name: str) -> bool:
    section = config.section("layouts")
    if name in section:
        del section[name]
        return True
    return False


# ---------------------------------------------------------------------------
# session file
# ---------------------------------------------------------------------------
def session_path() -> str:
    from . import config as config_mod

    return os.path.join(config_mod.state_dir(), "session.json")


def save_session(spec: LayoutSpec, path: Optional[str] = None) -> str:
    """Write the current layout to the session file."""
    target = path or session_path()
    directory = os.path.dirname(target)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = spec.to_dict()
    temporary = f"{target}.tmp{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, target)
    return target


def load_session(path: Optional[str] = None) -> Optional[LayoutSpec]:
    """Read the session file; ``None`` when missing or unusable."""
    target = path or session_path()
    if not os.path.exists(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parse_layout(payload, name="session")


def clear_session(path: Optional[str] = None) -> bool:
    target = path or session_path()
    try:
        os.unlink(target)
        return True
    except OSError:
        return False
