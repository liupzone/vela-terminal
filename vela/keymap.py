"""Keyboard shortcut parsing and registration.

Shortcuts are stored in a config file in GTK syntax (``<Ctrl><Shift>T``); this
module turns them into ``Gtk.Application`` accelerators and offers helpers for
resolving conflicts and displaying them in menus and the command palette.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

_MODIFIER_ALIASES = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "shift": "Shift",
    "alt": "Alt",
    "mod1": "Alt",
    "super": "Super",
    "meta": "Super",
    "win": "Super",
    "cmd": "Super",
}

_KEY_ALIASES = {
    "plus": "plus",
    "+": "plus",
    "equal": "plus",
    "minus": "minus",
    "-": "minus",
    "comma": "comma",
    ",": "comma",
    "period": "period",
    ".": "period",
    "slash": "slash",
    "escape": "Escape",
    "esc": "Escape",
    "enter": "Return",
    "return": "Return",
    "space": "space",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "page_up": "Page_Up",
    "pageup": "Page_Up",
    "page_down": "Page_Down",
    "pagedown": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "backtick": "grave",
}

_DISPLAY_NAMES = {
    "Ctrl": "Ctrl",
    "Shift": "Shift",
    "Alt": "Alt",
    "Super": "Super",
    "plus": "+",
    "minus": "−",
    "comma": ",",
    "period": ".",
    "grave": "`",
    "Page_Up": "PgUp",
    "Page_Down": "PgDn",
    "Return": "Enter",
    "Escape": "Esc",
    "BackSpace": "Bksp",
    "space": "Space",
    "Delete": "Del",
}

_VALID_KEYS = set(
    [
        "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
        "Up", "Down", "Left", "Right", "Home", "End", "Page_Up", "Page_Down",
        "Insert", "Delete", "BackSpace", "Return", "Escape", "Tab", "space",
        "grave", "plus", "minus", "comma", "period", "slash", "semicolon",
        "apostrophe", "backslash", "bracketleft", "bracketright",
    ]
)

# Canonical modifier order, so "<Shift><Ctrl>T" and "<Ctrl><Shift>T" normalise
# to the same accelerator and therefore conflict with each other.
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Super")


class ShortcutError(ValueError):
    pass


def parse(accelerator: str) -> Tuple[List[str], str]:
    """``"<Ctrl><Shift>T"`` -> ``(["Ctrl", "Shift"], "T")``."""
    text = (accelerator or "").strip()
    if not text:
        raise ShortcutError("empty accelerator")
    modifiers: List[str] = []
    index = 0
    while index < len(text) and text[index] == "<":
        end = text.find(">", index)
        if end == -1:
            raise ShortcutError(f"unterminated modifier in {accelerator!r}")
        name = text[index + 1 : end].strip().lower()
        canonical = _MODIFIER_ALIASES.get(name)
        if canonical is None:
            raise ShortcutError(f"unknown modifier {name!r} in {accelerator!r}")
        if canonical not in modifiers:
            modifiers.append(canonical)
        index = end + 1
    key = text[index:].strip()
    if not key:
        raise ShortcutError(f"missing key in {accelerator!r}")
    key = _normalise_key(key)
    modifiers.sort(key=_MODIFIER_ORDER.index)
    return modifiers, key


def _normalise_key(key: str) -> str:
    lowered = key.lower()
    if lowered in _KEY_ALIASES:
        return _KEY_ALIASES[lowered]
    if len(key) == 1:
        return key.upper()
    for valid in _VALID_KEYS:
        if valid.lower() == lowered:
            return valid
    if lowered.startswith("f") and lowered[1:].isdigit():
        return lowered.upper()
    raise ShortcutError(f"unknown key {key!r}")


def normalise(accelerator: str) -> str:
    """Canonical GTK accelerator string, or ``""`` when unparseable."""
    try:
        modifiers, key = parse(accelerator)
    except ShortcutError:
        return ""
    return "".join(f"<{name}>" for name in modifiers) + key


def display(accelerator: str) -> str:
    """Human-readable form for menus: ``Ctrl+Shift+T``."""
    try:
        modifiers, key = parse(accelerator)
    except ShortcutError:
        return accelerator or ""
    names = [_DISPLAY_NAMES.get(name, name) for name in modifiers]
    names.append(_DISPLAY_NAMES.get(key, key))
    return "+".join(names)


def find_conflicts(bindings: Dict[str, str]) -> List[Tuple[str, str, str]]:
    """Return ``(accelerator, action_a, action_b)`` for duplicated shortcuts."""
    seen: Dict[str, str] = {}
    conflicts: List[Tuple[str, str, str]] = []
    for action, accelerator in bindings.items():
        canonical = normalise(accelerator)
        if not canonical:
            continue
        other = seen.get(canonical)
        if other is not None:
            conflicts.append((canonical, other, action))
        else:
            seen[canonical] = action
    return conflicts


def apply(
    app: Gtk.Application,
    bindings: Dict[str, str],
    action_prefix: str = "app",
) -> List[str]:
    """Register ``bindings`` on ``app``; returns the accelerators rejected."""
    rejected: List[str] = []
    for action, accelerator in bindings.items():
        canonical = normalise(accelerator)
        if not canonical:
            if accelerator:
                rejected.append(accelerator)
            continue
        target = f"{action_prefix}.{action_name(action)}"
        try:
            app.set_accels_for_action(target, [canonical])
        except (TypeError, ValueError):
            rejected.append(accelerator)
    return rejected


def action_name(key: str) -> str:
    """Config keys use underscores; GAction names only allow dashes."""
    return key.replace("_", "-")


def matches(event: Gdk.EventKey, accelerator: str) -> bool:
    """Whether a key press event matches ``accelerator``."""
    try:
        modifiers, key = parse(accelerator)
    except ShortcutError:
        return False
    state = event.state & Gtk.accelerator_get_default_mod_mask()
    wanted = 0
    for name in modifiers:
        wanted |= {
            "Ctrl": Gdk.ModifierType.CONTROL_MASK,
            "Shift": Gdk.ModifierType.SHIFT_MASK,
            "Alt": Gdk.ModifierType.MOD1_MASK,
            "Super": Gdk.ModifierType.SUPER_MASK,
        }[name]
    if state != wanted:
        return False
    return Gtk.accelerator_name(event.keyval, 0).lower() == key.lower()


def iter_valid(bindings: Dict[str, str]) -> Iterable[Tuple[str, str]]:
    for action, accelerator in bindings.items():
        if normalise(accelerator):
            yield action, accelerator
