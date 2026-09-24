"""Configuration loading, validation and persistence.

Defaults live in this file so a missing config file is a normal, working state.
An unreadable or invalid file never blocks startup: we keep the defaults, record
the problem, and let the UI surface it.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from . import minitoml
from . import theme as theme_mod

CONFIG_DIR_ENV = "VELA_CONFIG_DIR"

FILE_HEADER = """Vela Terminal configuration
Generated automatically; every key below can be edited by hand.
Delete this file to fall back to built-in defaults.
"""

DEFAULTS: Dict[str, Any] = {
    "appearance": {
        "theme": theme_mod.DEFAULT_THEME,
        "font_family": "Ubuntu Mono",
        "fallback_font": "",
        "font_size": 12.0,
        "use_system_font": False,
        "cursor_shape": "block",
        "cursor_blink": "system",
        "scrollback_lines": 10000,
        "allow_bold": True,
        "bold_is_bright": True,
        "cell_width_scale": 1.0,
        "cell_height_scale": 1.0,
        "padding": 8,
        "opacity": 1.0,
        "hide_mouse_when_typing": True,
    },
    "behavior": {
        "shell": "",
        "login_shell": False,
        "working_directory": "",
        "scroll_on_output": False,
        "scroll_on_keystroke": True,
        "audible_bell": False,
        "visual_bell": True,
        "copy_on_select": False,
        "paste_on_middle_click": True,
        "confirm_close": True,
        "word_char_exceptions": "-_.:?/+#@%~=&",
        "allow_hyperlink": True,
        "show_exit_hint": True,
        "close_pane_on_exit": True,
        "close_window_on_last_exit": True,
        "close_on_abnormal_exit": False,
        "restore_session": False,
    },
    "window": {
        "width": 1080,
        "height": 680,
        "show_tabbar": "always",
        "show_statusbar": True,
        "show_headerbar": True,
        "tab_position": "top",
        "restore_working_directory": True,
        "sidebar_width": 300,
    },
    "sysinfo": {
        "enabled": True,
        "interval": 2.0,
        "hide_loopback": True,
        "max_interfaces": 3,
        "show_in_statusbar": True,
    },
    "files": {
        "open_internally": True,
        "ctrl_click_paths": True,
        "confirm_unsaved": True,
        "reveal_on_ctrl_shift_click": True,
    },
    "layouts": {},
    "filebrowser": {
        "enabled": False,
        "follow_terminal": True,
        "show_hidden": True,
        "icon_size": 16,
        "max_entries": 5000,
        "directories_first": True,
    },
    "keybindings": {
        "new_tab": "<Ctrl><Shift>T",
        "close_tab": "<Ctrl><Shift>W",
        "reopen_tab": "<Ctrl><Shift>N",
        "next_tab": "<Ctrl>Page_Down",
        "prev_tab": "<Ctrl>Page_Up",
        "move_tab_left": "<Ctrl><Shift>Page_Up",
        "move_tab_right": "<Ctrl><Shift>Page_Down",
        "new_window": "<Ctrl><Shift>I",
        "split_horizontal": "<Ctrl><Shift>O",
        "split_vertical": "<Ctrl><Shift>E",
        "close_pane": "<Ctrl><Shift>Q",
        "focus_left": "<Ctrl><Alt>Left",
        "focus_right": "<Ctrl><Alt>Right",
        "focus_up": "<Ctrl><Alt>Up",
        "focus_down": "<Ctrl><Alt>Down",
        "zoom_pane": "<Ctrl><Shift>Z",
        "find": "<Ctrl><Shift>F",
        "find_next": "<Ctrl><Shift>G",
        "find_previous": "<Ctrl><Shift>H",
        "command_palette": "<Ctrl><Shift>P",
        "toggle_filebrowser": "<Ctrl><Shift>D",
        "rename_tab": "<Ctrl><Shift>R",
        "save_layout": "<Ctrl><Shift>F2",
        "restore_layout": "<Ctrl><Shift>F3",
        "font_increase": "<Ctrl>plus",
        "font_decrease": "<Ctrl>minus",
        "font_reset": "<Ctrl>0",
        "copy": "<Ctrl><Shift>C",
        "paste": "<Ctrl><Shift>V",
        "paste_escaped": "<Ctrl><Shift>B",
        "select_all": "<Ctrl><Shift>A",
        "clear": "<Ctrl><Shift>L",
        "reset": "<Ctrl><Shift>K",
        "toggle_fullscreen": "F11",
        "open_preferences": "<Ctrl>comma",
        "quit": "<Ctrl><Shift>X",
        "toggle_statusbar": "<Ctrl><Shift>S",
        "toggle_sysinfo": "<Ctrl><Shift>M",
        "open_path": "<Ctrl><Shift>Return",
        "open_selection": "<Ctrl>Return",
        "reveal_path": "<Ctrl><Shift>F4",
    },
}

_VALID_TAB_POSITION = ("top", "bottom")
_VALID_SHOW_TABBAR = ("always", "multiple", "never")
_VALID_CURSOR_SHAPE = ("block", "ibeam", "underline")
_VALID_CURSOR_BLINK = ("off", "on", "system")


def config_home() -> str:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return override
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "vela")


def config_path() -> str:
    return os.path.join(config_home(), "config.toml")


def state_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "vela")


def _deep_copy(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: _deep_copy(value) for key, value in data.items()}
    if isinstance(data, list):
        return list(data)
    return data


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``override`` into ``base``, keeping unknown keys for round-trips."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    return fallback


def _coerce_float(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _coerce_int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _coerce_choice(value: Any, choices: Tuple[str, ...], fallback: str) -> str:
    if isinstance(value, str) and value.strip().lower() in choices:
        return value.strip().lower()
    return fallback


class Config:
    """Validated view over the merged default + user configuration."""

    def __init__(
        self,
        data: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
        problems: Optional[List[str]] = None,
    ) -> None:
        self.path = path or config_path()
        self.problems: List[str] = list(problems or [])
        self._data = _deep_copy(DEFAULTS)
        if data:
            _merge(self._data, data)
        self._validate()

    # -- loading ---------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        target = path or config_path()
        problems: List[str] = []
        data: Dict[str, Any] = {}
        if os.path.exists(target):
            try:
                with open(target, "r", encoding="utf-8") as handle:
                    data = minitoml.loads(handle.read())
            except minitoml.TomlError as error:
                problems.append(f"配置文件语法错误：{error}")
            except OSError as error:
                problems.append(f"配置文件无法读取：{error}")
            except UnicodeDecodeError as error:
                problems.append(f"配置文件编码错误：{error}")
        return cls(data, target, problems)

    # -- validation ------------------------------------------------------
    def _validate(self) -> None:
        appearance = self._data["appearance"]
        behavior = self._data["behavior"]
        window = self._data["window"]
        keys = self._data["keybindings"]

        if appearance.get("theme") not in theme_mod.all_names():
            self.problems.append(
                f"未知主题 {appearance.get('theme')!r}，已回退到 "
                f"{theme_mod.DEFAULT_THEME!r}"
            )
            appearance["theme"] = theme_mod.DEFAULT_THEME

        appearance["font_family"] = str(
            appearance.get("font_family") or DEFAULTS["appearance"]["font_family"]
        ).strip() or DEFAULTS["appearance"]["font_family"]
        appearance["fallback_font"] = str(
            appearance.get("fallback_font") or ""
        ).strip()
        appearance["font_size"] = _coerce_float(
            appearance.get("font_size"), 12.0, 5.0, 72.0
        )
        appearance["use_system_font"] = _coerce_bool(
            appearance.get("use_system_font"), False
        )
        appearance["cursor_shape"] = _coerce_choice(
            appearance.get("cursor_shape"), _VALID_CURSOR_SHAPE, "block"
        )
        appearance["cursor_blink"] = _coerce_choice(
            appearance.get("cursor_blink"), _VALID_CURSOR_BLINK, "system"
        )
        appearance["scrollback_lines"] = _coerce_int(
            appearance.get("scrollback_lines"), 10000, 0, 1_000_000
        )
        appearance["allow_bold"] = _coerce_bool(appearance.get("allow_bold"), True)
        appearance["bold_is_bright"] = _coerce_bool(
            appearance.get("bold_is_bright"), True
        )
        appearance["cell_width_scale"] = _coerce_float(
            appearance.get("cell_width_scale"), 1.0, 0.5, 2.0
        )
        appearance["cell_height_scale"] = _coerce_float(
            appearance.get("cell_height_scale"), 1.0, 0.5, 2.0
        )
        appearance["padding"] = _coerce_int(appearance.get("padding"), 8, 0, 64)
        appearance["opacity"] = _coerce_float(appearance.get("opacity"), 1.0, 0.3, 1.0)
        appearance["hide_mouse_when_typing"] = _coerce_bool(
            appearance.get("hide_mouse_when_typing"), True
        )

        for key in (
            "login_shell",
            "scroll_on_output",
            "scroll_on_keystroke",
            "audible_bell",
            "visual_bell",
            "copy_on_select",
            "paste_on_middle_click",
            "confirm_close",
            "allow_hyperlink",
            "show_exit_hint",
            "close_pane_on_exit",
            "close_window_on_last_exit",
            "close_on_abnormal_exit",
            "restore_session",
        ):
            behavior[key] = _coerce_bool(behavior.get(key), DEFAULTS["behavior"][key])
        for key in ("shell", "working_directory", "word_char_exceptions"):
            behavior[key] = str(behavior.get(key) or "")
        if not behavior["word_char_exceptions"]:
            behavior["word_char_exceptions"] = DEFAULTS["behavior"][
                "word_char_exceptions"
            ]

        window["width"] = _coerce_int(window.get("width"), 1080, 320, 20000)
        window["height"] = _coerce_int(window.get("height"), 680, 200, 20000)
        window["sidebar_width"] = _coerce_int(
            window.get("sidebar_width"), 300, 200, 900
        )
        window["show_tabbar"] = _coerce_choice(
            window.get("show_tabbar"), _VALID_SHOW_TABBAR, "always"
        )
        window["tab_position"] = _coerce_choice(
            window.get("tab_position"), _VALID_TAB_POSITION, "top"
        )
        for key in (
            "show_statusbar",
            "show_headerbar",
            "restore_working_directory",
        ):
            window[key] = _coerce_bool(window.get(key), DEFAULTS["window"][key])

        for action, default in DEFAULTS["keybindings"].items():
            value = keys.get(action, default)
            keys[action] = str(value).strip() if value is not None else ""

        sysinfo = self._data["sysinfo"]
        files_section = self._data["files"]
        sysinfo["enabled"] = _coerce_bool(sysinfo.get("enabled"), True)
        sysinfo["interval"] = _coerce_float(sysinfo.get("interval"), 2.0, 0.5, 60.0)
        sysinfo["hide_loopback"] = _coerce_bool(sysinfo.get("hide_loopback"), True)
        sysinfo["max_interfaces"] = _coerce_int(
            sysinfo.get("max_interfaces"), 3, 1, 16
        )
        sysinfo["show_in_statusbar"] = _coerce_bool(
            sysinfo.get("show_in_statusbar"), True
        )
        for key in (
            "open_internally",
            "ctrl_click_paths",
            "confirm_unsaved",
            "reveal_on_ctrl_shift_click",
        ):
            files_section[key] = _coerce_bool(
                files_section.get(key), DEFAULTS["files"][key]
            )

        browser = self._data["filebrowser"]
        browser["enabled"] = _coerce_bool(
            browser.get("enabled"), DEFAULTS["filebrowser"]["enabled"]
        )
        browser["follow_terminal"] = _coerce_bool(
            browser.get("follow_terminal"), True
        )
        browser["show_hidden"] = _coerce_bool(browser.get("show_hidden"), True)
        browser["icon_size"] = _coerce_int(browser.get("icon_size"), 16, 8, 64)
        browser["max_entries"] = _coerce_int(
            browser.get("max_entries"), 5000, 100, 200000
        )
        browser["directories_first"] = _coerce_bool(
            browser.get("directories_first"), True
        )

        # Keys that moved or were replaced.  They are dropped so a saved config
        # does not keep advertising settings that no longer do anything.
        self._drop_obsolete(
            {
                ("sysinfo", "side"): "window.sidebar_width 现在统一控制侧栏宽度",
                ("sysinfo", "width"): "请改用 window.sidebar_width",
                ("filebrowser", "width"): "请改用 window.sidebar_width",
            }
        )

    def _drop_obsolete(self, mapping: Dict[tuple, str]) -> None:
        """Remove superseded keys, telling the user where the setting went."""
        for (section, key), hint in mapping.items():
            node = self._data.get(section)
            if isinstance(node, dict) and key in node:
                node.pop(key, None)
                self.problems.append(f"配置项 {section}.{key} 已废弃：{hint}")

    # -- access ----------------------------------------------------------
    def get(self, dotted: str, fallback: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return fallback
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def section(self, name: str) -> Dict[str, Any]:
        value = self._data.get(name, {})
        return value if isinstance(value, dict) else {}

    def as_dict(self) -> Dict[str, Any]:
        return _deep_copy(self._data)

    def keybindings(self) -> Dict[str, str]:
        return dict(self.section("keybindings"))

    def theme(self) -> theme_mod.Theme:
        return theme_mod.get(self.get("appearance.theme"))

    def flattened(self) -> List[Tuple[str, Any]]:
        items: List[Tuple[str, Any]] = []

        def walk(node: Any, prefix: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{prefix}.{key}" if prefix else key)
            else:
                items.append((prefix, node))

        walk(self._data, "")
        return items

    # -- saving ----------------------------------------------------------
    def to_toml(self) -> str:
        return minitoml.dumps(self._data, FILE_HEADER)

    def save(self, path: Optional[str] = None) -> str:
        target = path or self.path
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        text = self.to_toml()
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as handle:
                if handle.read() == text:
                    return target
        temporary = f"{target}.tmp{os.getpid()}"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, target)
        return target

    def reload(self) -> "Config":
        fresh = Config.load(self.path)
        self._data = fresh._data
        self.problems = fresh.problems
        return self
