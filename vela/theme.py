"""Colour themes for Vela.

A theme carries two things: the 16-colour ANSI palette handed to VTE, and a set
of UI colours used to render GTK CSS.  Both are derived from one authored
definition so a theme can never drift out of sync with its terminal colours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

ANSI_NAMES = (
    "black",
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
    "bright_black",
    "bright_red",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_magenta",
    "bright_cyan",
    "bright_white",
)


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    dark: bool
    background: str
    foreground: str
    cursor: str
    cursor_text: str
    selection: str
    selection_text: str
    accent: str
    palette: Dict[str, str]
    ui: Dict[str, str] = field(default_factory=dict)

    def ansi(self) -> List[str]:
        """Palette as an ordered list, index 0..15."""
        return [self.palette[key] for key in ANSI_NAMES]

    def color(self, key: str, fallback: str = "#000000") -> str:
        """Look up a UI colour, falling back to a sensible derived value."""
        if key in self.ui:
            return self.ui[key]
        if key in self.palette:
            return self.palette[key]
        if key == "background":
            return self.background
        if key == "foreground":
            return self.foreground
        if key == "accent":
            return self.accent
        return fallback

    def to_dict(self) -> Dict[str, object]:
        data: Dict[str, object] = {
            "name": self.name,
            "label": self.label,
            "dark": self.dark,
            "background": self.background,
            "foreground": self.foreground,
            "cursor": self.cursor,
            "cursor_text": self.cursor_text,
            "selection": self.selection,
            "selection_text": self.selection_text,
            "accent": self.accent,
        }
        data.update({key: value for key, value in self.ui.items()})
        data.update(self.palette)
        return data


def _palette(*colors: str) -> Dict[str, str]:
    if len(colors) != 16:
        raise ValueError(f"a palette needs 16 colours, got {len(colors)}")
    return dict(zip(ANSI_NAMES, colors))


def _ui(**kwargs: str) -> Dict[str, str]:
    return dict(kwargs)


_THEMES: Dict[str, Theme] = {}


def register(theme: Theme) -> Theme:
    _THEMES[theme.name] = theme
    return theme


def names() -> List[str]:
    return list(_THEMES)


def get(name: Optional[str]) -> Theme:
    if name and name in _THEMES:
        return _THEMES[name]
    if name == AUTO_THEME:
        return _system_theme()
    return _THEMES[DEFAULT_THEME]


def describe() -> List[str]:
    rows = [f"{AUTO_THEME:<18} 跟随系统"]
    rows.extend(f"{theme.name:<18} {theme.label}" for theme in _THEMES.values())
    return rows


def all_names() -> List[str]:
    return [AUTO_THEME] + list(_THEMES)


def label(name: str) -> str:
    if name == AUTO_THEME:
        return "跟随系统"
    theme = _THEMES.get(name)
    return theme.label if theme else name


def _system_theme() -> Theme:
    """Pick dark or light based on the current GTK theme."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        settings = Gtk.Settings.get_default()
        if settings is not None:
            prefer_dark = bool(
                settings.get_property("gtk-application-prefer-dark-theme")
            )
            name = str(settings.get_property("gtk-theme-name") or "").lower()
            if prefer_dark or "dark" in name:
                return _THEMES["vela-dark"]
    except Exception:  # pragma: no cover - no display / no GTK
        pass
    return _THEMES["vela-light"]


DEFAULT_THEME = "vela-dark"
AUTO_THEME = "auto"


register(
    Theme(
        name="vela-dark",
        label="Vela Dark（默认）",
        dark=True,
        background="#12141c",
        foreground="#d7dae6",
        cursor="#7aa2f7",
        cursor_text="#12141c",
        selection="#2f3b5c",
        selection_text="#e6e9f5",
        accent="#7aa2f7",
        palette=_palette(
            "#262a36", "#f07178", "#7ec699", "#e6c07b",
            "#7aa2f7", "#c099ff", "#6fc3d9", "#c8ccd8",
            "#4c5366", "#ff8b92", "#9ee7b4", "#ffd68a",
            "#9ab8ff", "#d6b3ff", "#8fdcf0", "#f2f4fa",
        ),
        ui=_ui(
            headerbar_bg="#171a24",
            headerbar_fg="#d7dae6",
            headerbar_border="#262a36",
            tab_active_bg="#12141c",
            tab_inactive_bg="#171a24",
            tab_hover_bg="#1e222e",
            tab_fg="#8b91a3",
            tab_active_fg="#e6e9f5",
            statusbar_bg="#171a24",
            statusbar_fg="#8b91a3",
            border="#262a36",
            overlay_bg="#1b1f2b",
            overlay_fg="#d7dae6",
            overlay_border="#323848",
            search_bg="#1b1f2b",
            search_match="#3d5a99",
            scrollbar="#2c3242",
            split_border="#1a1d28",
            search_hit="#e6c07b",
            search_hit_current="#ff9d5c",
        ),
    )
)

register(
    Theme(
        name="vela-light",
        label="Vela Light",
        dark=False,
        background="#fbfbfd",
        foreground="#2b2f3a",
        cursor="#3b6ef5",
        cursor_text="#fbfbfd",
        selection="#cdd9fb",
        selection_text="#1d2230",
        accent="#3b6ef5",
        palette=_palette(
            "#3b4252", "#c53b4c", "#2f8f57", "#a86a1b",
            "#2f5fd0", "#8a4ec9", "#0f7f92", "#6b7280",
            "#7a8494", "#d4566a", "#3aa76d", "#c2831f",
            "#4a78e8", "#a066e0", "#1c93a8", "#1f2430",
        ),
        ui=_ui(
            headerbar_bg="#f1f2f6",
            headerbar_fg="#2b2f3a",
            headerbar_border="#dcdfe8",
            tab_active_bg="#fbfbfd",
            tab_inactive_bg="#eceef4",
            tab_hover_bg="#e3e6ee",
            tab_fg="#6b7280",
            tab_active_fg="#1f2430",
            statusbar_bg="#f1f2f6",
            statusbar_fg="#6b7280",
            border="#dcdfe8",
            overlay_bg="#ffffff",
            overlay_fg="#2b2f3a",
            overlay_border="#d3d7e2",
            search_bg="#ffffff",
            search_match="#ffe08a",
            scrollbar="#c7ccd8",
            split_border="#e4e6ee",
            search_hit="#ffe08a",
            search_hit_current="#ffb454",
        ),
    )
)

register(
    Theme(
        name="dracula",
        label="Dracula",
        dark=True,
        background="#282a36",
        foreground="#f8f8f2",
        cursor="#f8f8f2",
        cursor_text="#282a36",
        selection="#44475a",
        selection_text="#f8f8f2",
        accent="#bd93f9",
        palette=_palette(
            "#21222c", "#ff5555", "#50fa7b", "#f1fa8c",
            "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
            "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5",
            "#d6acff", "#ff92df", "#a4ffff", "#ffffff",
        ),
        ui=_ui(
            headerbar_bg="#21222c",
            headerbar_fg="#f8f8f2",
            headerbar_border="#191a21",
            tab_active_bg="#282a36",
            tab_inactive_bg="#21222c",
            tab_hover_bg="#343746",
            tab_fg="#a8aec4",
            tab_active_fg="#f8f8f2",
            statusbar_bg="#21222c",
            statusbar_fg="#a8aec4",
            border="#191a21",
            overlay_bg="#21222c",
            overlay_fg="#f8f8f2",
            overlay_border="#44475a",
            search_bg="#21222c",
            search_match="#6272a4",
            scrollbar="#44475a",
            split_border="#1d1e26",
            search_hit="#f1fa8c",
            search_hit_current="#ffb86c",
        ),
    )
)

register(
    Theme(
        name="nord",
        label="Nord",
        dark=True,
        background="#2e3440",
        foreground="#d8dee9",
        cursor="#88c0d0",
        cursor_text="#2e3440",
        selection="#434c5e",
        selection_text="#eceff4",
        accent="#88c0d0",
        palette=_palette(
            "#3b4252", "#bf616a", "#a3be8c", "#ebcb8b",
            "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
            "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b",
            "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4",
        ),
        ui=_ui(
            headerbar_bg="#292e39",
            headerbar_fg="#d8dee9",
            headerbar_border="#242933",
            tab_active_bg="#2e3440",
            tab_inactive_bg="#292e39",
            tab_hover_bg="#3b4252",
            tab_fg="#94a0b8",
            tab_active_fg="#eceff4",
            statusbar_bg="#292e39",
            statusbar_fg="#94a0b8",
            border="#242933",
            overlay_bg="#292e39",
            overlay_fg="#d8dee9",
            overlay_border="#4c566a",
            search_bg="#292e39",
            search_match="#4c566a",
            scrollbar="#434c5e",
            split_border="#232833",
            search_hit="#ebcb8b",
            search_hit_current="#d08770",
        ),
    )
)

register(
    Theme(
        name="tokyo-night",
        label="Tokyo Night",
        dark=True,
        background="#1a1b26",
        foreground="#c0caf5",
        cursor="#c0caf5",
        cursor_text="#1a1b26",
        selection="#33467c",
        selection_text="#c0caf5",
        accent="#7aa2f7",
        palette=_palette(
            "#15161e", "#f7768e", "#9ece6a", "#e0af68",
            "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
            "#414868", "#f7768e", "#9ece6a", "#e0af68",
            "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5",
        ),
        ui=_ui(
            headerbar_bg="#16161e",
            headerbar_fg="#c0caf5",
            headerbar_border="#101014",
            tab_active_bg="#1a1b26",
            tab_inactive_bg="#16161e",
            tab_hover_bg="#24283b",
            tab_fg="#8b93b8",
            tab_active_fg="#c0caf5",
            statusbar_bg="#16161e",
            statusbar_fg="#8b93b8",
            border="#101014",
            overlay_bg="#1f2335",
            overlay_fg="#c0caf5",
            overlay_border="#3b4261",
            search_bg="#1f2335",
            search_match="#3d59a1",
            scrollbar="#2f334d",
            split_border="#14151d",
            search_hit="#e0af68",
            search_hit_current="#ff9e64",
        ),
    )
)

register(
    Theme(
        name="catppuccin-mocha",
        label="Catppuccin Mocha",
        dark=True,
        background="#1e1e2e",
        foreground="#cdd6f4",
        cursor="#f5e0dc",
        cursor_text="#1e1e2e",
        selection="#585b70",
        selection_text="#cdd6f4",
        accent="#cba6f7",
        palette=_palette(
            "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
            "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
            "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af",
            "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
        ),
        ui=_ui(
            headerbar_bg="#181825",
            headerbar_fg="#cdd6f4",
            headerbar_border="#11111b",
            tab_active_bg="#1e1e2e",
            tab_inactive_bg="#181825",
            tab_hover_bg="#313244",
            tab_fg="#9399b2",
            tab_active_fg="#cdd6f4",
            statusbar_bg="#181825",
            statusbar_fg="#9399b2",
            border="#11111b",
            overlay_bg="#181825",
            overlay_fg="#cdd6f4",
            overlay_border="#45475a",
            search_bg="#181825",
            search_match="#585b70",
            scrollbar="#45475a",
            split_border="#15151f",
            search_hit="#f9e2af",
            search_hit_current="#fab387",
        ),
    )
)

register(
    Theme(
        name="gruvbox-dark",
        label="Gruvbox Dark",
        dark=True,
        background="#282828",
        foreground="#ebdbb2",
        cursor="#ebdbb2",
        cursor_text="#282828",
        selection="#504945",
        selection_text="#fbf1c7",
        accent="#fabd2f",
        palette=_palette(
            "#282828", "#cc241d", "#98971a", "#d79921",
            "#458588", "#b16286", "#689d6a", "#a89984",
            "#928374", "#fb4934", "#b8bb26", "#fabd2f",
            "#83a598", "#d3869b", "#8ec07c", "#ebdbb2",
        ),
        ui=_ui(
            headerbar_bg="#1d2021",
            headerbar_fg="#ebdbb2",
            headerbar_border="#141414",
            tab_active_bg="#282828",
            tab_inactive_bg="#1d2021",
            tab_hover_bg="#3c3836",
            tab_fg="#a89984",
            tab_active_fg="#ebdbb2",
            statusbar_bg="#1d2021",
            statusbar_fg="#a89984",
            border="#141414",
            overlay_bg="#1d2021",
            overlay_fg="#ebdbb2",
            overlay_border="#504945",
            search_bg="#1d2021",
            search_match="#504945",
            scrollbar="#3c3836",
            split_border="#1a1a1a",
            search_hit="#fabd2f",
            search_hit_current="#fe8019",
        ),
    )
)

register(
    Theme(
        name="solarized-dark",
        label="Solarized Dark",
        dark=True,
        background="#002b36",
        foreground="#839496",
        cursor="#93a1a1",
        cursor_text="#002b36",
        selection="#073642",
        selection_text="#eee8d5",
        accent="#268bd2",
        palette=_palette(
            "#073642", "#dc322f", "#859900", "#b58900",
            "#268bd2", "#d33682", "#2aa198", "#eee8d5",
            "#002b36", "#cb4b16", "#586e75", "#657b83",
            "#839496", "#6c71c4", "#93a1a1", "#fdf6e3",
        ),
        ui=_ui(
            headerbar_bg="#00252e",
            headerbar_fg="#93a1a1",
            headerbar_border="#001f27",
            tab_active_bg="#002b36",
            tab_inactive_bg="#00252e",
            tab_hover_bg="#073642",
            tab_fg="#657b83",
            tab_active_fg="#eee8d5",
            statusbar_bg="#00252e",
            statusbar_fg="#657b83",
            border="#001f27",
            overlay_bg="#00252e",
            overlay_fg="#93a1a1",
            overlay_border="#0b4453",
            search_bg="#00252e",
            search_match="#0b4453",
            scrollbar="#0b4453",
            split_border="#001c23",
            search_hit="#b58900",
            search_hit_current="#cb4b16",
        ),
    )
)

register(
    Theme(
        name="solarized-light",
        label="Solarized Light",
        dark=False,
        background="#fdf6e3",
        foreground="#586e75",
        cursor="#586e75",
        cursor_text="#fdf6e3",
        selection="#eee8d5",
        selection_text="#073642",
        accent="#268bd2",
        palette=_palette(
            "#073642", "#dc322f", "#859900", "#b58900",
            "#268bd2", "#d33682", "#2aa198", "#eee8d5",
            "#002b36", "#cb4b16", "#586e75", "#657b83",
            "#839496", "#6c71c4", "#93a1a1", "#fdf6e3",
        ),
        ui=_ui(
            headerbar_bg="#eee8d5",
            headerbar_fg="#586e75",
            headerbar_border="#ded8c4",
            tab_active_bg="#fdf6e3",
            tab_inactive_bg="#eee8d5",
            tab_hover_bg="#e4ddc8",
            tab_fg="#7a8f96",
            tab_active_fg="#073642",
            statusbar_bg="#eee8d5",
            statusbar_fg="#7a8f96",
            border="#ded8c4",
            overlay_bg="#fdf6e3",
            overlay_fg="#586e75",
            overlay_border="#d5cdb6",
            search_bg="#fdf6e3",
            search_match="#e8dfae",
            scrollbar="#d5cdb6",
            split_border="#e8e2d0",
            search_hit="#e8dfae",
            search_hit_current="#f0b429",
        ),
    )
)

register(
    Theme(
        name="one-dark",
        label="One Dark",
        dark=True,
        background="#282c34",
        foreground="#abb2bf",
        cursor="#528bff",
        cursor_text="#282c34",
        selection="#3e4451",
        selection_text="#abb2bf",
        accent="#61afef",
        palette=_palette(
            "#282c34", "#e06c75", "#98c379", "#e5c07b",
            "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
            "#5c6370", "#e06c75", "#98c379", "#e5c07b",
            "#61afef", "#c678dd", "#56b6c2", "#ffffff",
        ),
        ui=_ui(
            headerbar_bg="#21252b",
            headerbar_fg="#abb2bf",
            headerbar_border="#181a1f",
            tab_active_bg="#282c34",
            tab_inactive_bg="#21252b",
            tab_hover_bg="#2c313a",
            tab_fg="#7f848e",
            tab_active_fg="#d7dae0",
            statusbar_bg="#21252b",
            statusbar_fg="#7f848e",
            border="#181a1f",
            overlay_bg="#21252b",
            overlay_fg="#abb2bf",
            overlay_border="#3e4451",
            search_bg="#21252b",
            search_match="#3e4451",
            scrollbar="#3e4451",
            split_border="#1b1e23",
            search_hit="#e5c07b",
            search_hit_current="#d19a66",
        ),
    )
)


def _add(
    name: str,
    label: str,
    dark: bool,
    background: str,
    foreground: str,
    cursor: str,
    cursor_text: str,
    selection: str,
    selection_text: str,
    accent: str,
    palette: tuple,
    ui: dict,
) -> None:
    """Register a theme from the compact table below.

    Every theme in this section was written by hand from its published palette;
    ``_add`` only saves repeating the dataclass boilerplate, it does not derive
    any colour, so each theme stays fully explicit and reviewable.
    """
    register(
        Theme(
            name=name,
            label=label,
            dark=dark,
            background=background,
            foreground=foreground,
            cursor=cursor,
            cursor_text=cursor_text,
            selection=selection,
            selection_text=selection_text,
            accent=accent,
            palette=_palette(*palette),
            ui=_ui(**ui),
        )
    )


def _surface(
    bg: str,
    fg: str,
    border: str,
    active_bg: str,
    inactive_bg: str,
    hover_bg: str,
    dim_fg: str,
    active_fg: str,
    overlay: str,
    overlay_fg: str,
    overlay_border: str,
    scrollbar: str,
    split: str,
    hit: str,
    hit_current: str,
    search_match: str,
) -> dict:
    """Build the UI colour dict; every theme uses the same key set."""
    return {
        "headerbar_bg": inactive_bg,
        "headerbar_fg": overlay_fg,
        "headerbar_border": border,
        "tab_active_bg": active_bg,
        "tab_inactive_bg": inactive_bg,
        "tab_hover_bg": hover_bg,
        "tab_fg": dim_fg,
        "tab_active_fg": active_fg,
        "statusbar_bg": inactive_bg,
        "statusbar_fg": dim_fg,
        "border": border,
        "overlay_bg": overlay,
        "overlay_fg": overlay_fg,
        "overlay_border": overlay_border,
        "search_bg": overlay,
        "search_match": search_match,
        "scrollbar": scrollbar,
        "split_border": split,
        "search_hit": hit,
        "search_hit_current": hit_current,
    }


# ---------------------------------------------------------------------------
# Additional themes
# ---------------------------------------------------------------------------
_add(
    "catppuccin-latte", "Catppuccin Latte", False,
    "#eff1f5", "#4c4f69", "#dc8a78", "#eff1f5", "#ccd0da", "#4c4f69", "#1e66f5",
    ("#5c5f77", "#d20f39", "#40a02b", "#df8e1d",
     "#1e66f5", "#ea76cb", "#179299", "#acb0be",
     "#6c6f85", "#d20f39", "#40a02b", "#df8e1d",
     "#1e66f5", "#ea76cb", "#179299", "#bcc0cc"),
    _surface("#eff1f5", "#4c4f69", "#dce0e8", "#eff1f5", "#e6e9ef", "#dce0e8",
             "#8c8fa1", "#4c4f69", "#ffffff", "#4c4f69", "#ccd0da",
             "#ccd0da", "#e6e9ef", "#df8e1d", "#fe640b", "#bcc0cc"),
)

_add(
    "catppuccin-frappe", "Catppuccin Frappé", True,
    "#303446", "#c6d0f5", "#f2d5cf", "#303446", "#51576d", "#c6d0f5", "#ca9ee6",
    ("#51576d", "#e78284", "#a6d189", "#e5c890",
     "#8caaee", "#f4b8e4", "#81c8be", "#b5bfe2",
     "#626880", "#e78284", "#a6d189", "#e5c890",
     "#8caaee", "#f4b8e4", "#81c8be", "#a5adce"),
    _surface("#303446", "#c6d0f5", "#232634", "#303446", "#292c3c", "#414559",
             "#838ba7", "#c6d0f5", "#292c3c", "#c6d0f5", "#51576d",
             "#51576d", "#232634", "#e5c890", "#ef9f76", "#626880"),
)

_add(
    "catppuccin-macchiato", "Catppuccin Macchiato", True,
    "#24273a", "#cad3f5", "#f4dbd6", "#24273a", "#494d64", "#cad3f5", "#c6a0f6",
    ("#494d64", "#ed8796", "#a6da95", "#eed49f",
     "#8aadf4", "#f5bde6", "#8bd5ca", "#b8c0e0",
     "#5b6078", "#ed8796", "#a6da95", "#eed49f",
     "#8aadf4", "#f5bde6", "#8bd5ca", "#a5adcb"),
    _surface("#24273a", "#cad3f5", "#1e2030", "#24273a", "#1e2030", "#363a4f",
             "#8087a2", "#cad3f5", "#1e2030", "#cad3f5", "#494d64",
             "#494d64", "#1e2030", "#eed49f", "#f5a97f", "#5b6078"),
)

_add(
    "gruvbox-light", "Gruvbox Light", False,
    "#fbf1c7", "#3c3836", "#af3a03", "#fbf1c7", "#ebdbb2", "#3c3836", "#076678",
    ("#fbf1c7", "#9d0006", "#79740e", "#b57614",
     "#076678", "#8f3f71", "#427b58", "#7c6f64",
     "#928374", "#cc241d", "#98971a", "#d79921",
     "#458588", "#b16286", "#689d6a", "#3c3836"),
    _surface("#fbf1c7", "#3c3836", "#ebdbb2", "#fbf1c7", "#f2e5bc", "#ebdbb2",
             "#928374", "#3c3836", "#f9f5d7", "#3c3836", "#d5c4a1",
             "#d5c4a1", "#f2e5bc", "#b57614", "#d65d0e", "#e0cfa9"),
)

_add(
    "tokyo-night-storm", "Tokyo Night Storm", True,
    "#24283b", "#c0caf5", "#c0caf5", "#24283b", "#364a82", "#c0caf5", "#7aa2f7",
    ("#1d202f", "#f7768e", "#9ece6a", "#e0af68",
     "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
     "#414868", "#f7768e", "#9ece6a", "#e0af68",
     "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5"),
    _surface("#24283b", "#c0caf5", "#1b1e2d", "#24283b", "#1f2335", "#2f334d",
             "#8b93b8", "#c0caf5", "#1f2335", "#c0caf5", "#3b4261",
             "#2f334d", "#1b1e2d", "#e0af68", "#ff9e64", "#3d59a1"),
)

_add(
    "night-owl", "Night Owl", True,
    "#011627", "#d6deeb", "#80a4c2", "#011627", "#1d3b53", "#d6deeb", "#82aaff",
    ("#011627", "#ef5350", "#22da6e", "#addb67",
     "#82aaff", "#c792ea", "#21c7a8", "#d6deeb",
     "#575656", "#ef5350", "#22da6e", "#ffeb95",
     "#82aaff", "#c792ea", "#7fdbca", "#ffffff"),
    _surface("#011627", "#d6deeb", "#0b2942", "#011627", "#01111d", "#0b2942",
             "#5f7e97", "#d6deeb", "#01111d", "#d6deeb", "#1d3b53",
             "#1d3b53", "#01111d", "#addb67", "#ffcb8b", "#1d3b53"),
)

_add(
    "palenight", "Palenight", True,
    "#292d3e", "#a6accd", "#ffcc00", "#292d3e", "#444267", "#a6accd", "#82aaff",
    ("#292d3e", "#f07178", "#c3e88d", "#ffcb6b",
     "#82aaff", "#c792ea", "#89ddff", "#d0d3e8",
     "#676e95", "#f07178", "#c3e88d", "#ffcb6b",
     "#82aaff", "#c792ea", "#89ddff", "#ffffff"),
    _surface("#292d3e", "#a6accd", "#1b1e2b", "#292d3e", "#1e2132", "#34324a",
             "#676e95", "#a6accd", "#1e2132", "#a6accd", "#444267",
             "#444267", "#1b1e2b", "#ffcb6b", "#f78c6c", "#444267"),
)

_add(
    "ayu-dark", "Ayu Dark", True,
    "#0a0e14", "#b3b1ad", "#e6b450", "#0a0e14", "#273747", "#b3b1ad", "#e6b450",
    ("#01060e", "#ea6c73", "#91b362", "#f9af4f",
     "#53bdfa", "#fae994", "#90e1c6", "#c7c7c7",
     "#686868", "#f07178", "#c2d94c", "#ffb454",
     "#59c2ff", "#ffee99", "#95e6cb", "#ffffff"),
    _surface("#0a0e14", "#b3b1ad", "#01060e", "#0a0e14", "#01060e", "#1f2430",
             "#626a73", "#b3b1ad", "#01060e", "#b3b1ad", "#273747",
             "#273747", "#01060e", "#f9af4f", "#ff8f40", "#273747"),
)

_add(
    "ayu-mirage", "Ayu Mirage", True,
    "#1f2430", "#cbccc6", "#ffcc66", "#1f2430", "#33415e", "#cbccc6", "#ffcc66",
    ("#191e2a", "#f28779", "#bae67e", "#ffd580",
     "#73d0ff", "#d4bfff", "#95e6cb", "#c7c7c7",
     "#686868", "#f28779", "#bae67e", "#ffd580",
     "#73d0ff", "#d4bfff", "#95e6cb", "#ffffff"),
    _surface("#1f2430", "#cbccc6", "#171b24", "#1f2430", "#171b24", "#2b3243",
             "#707a8c", "#cbccc6", "#171b24", "#cbccc6", "#33415e",
             "#33415e", "#171b24", "#ffd580", "#ffa759", "#33415e"),
)

_add(
    "rose-pine", "Rosé Pine", True,
    "#191724", "#e0def4", "#524f67", "#e0def4", "#403d52", "#e0def4", "#c4a7e7",
    ("#26233a", "#eb6f92", "#31748f", "#f6c177",
     "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4",
     "#6e6a86", "#eb6f92", "#31748f", "#f6c177",
     "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4"),
    _surface("#191724", "#e0def4", "#161320", "#191724", "#1f1d2e", "#26233a",
             "#6e6a86", "#e0def4", "#1f1d2e", "#e0def4", "#403d52",
             "#403d52", "#161320", "#f6c177", "#eb6f92", "#403d52"),
)

_add(
    "rose-pine-moon", "Rosé Pine Moon", True,
    "#232136", "#e0def4", "#56526e", "#e0def4", "#44415a", "#e0def4", "#c4a7e7",
    ("#393552", "#eb6f92", "#3e8fb0", "#f6c177",
     "#9ccfd8", "#c4a7e7", "#ea9a97", "#e0def4",
     "#6e6a86", "#eb6f92", "#3e8fb0", "#f6c177",
     "#9ccfd8", "#c4a7e7", "#ea9a97", "#e0def4"),
    _surface("#232136", "#e0def4", "#1f1d30", "#232136", "#2a273f", "#393552",
             "#817c9c", "#e0def4", "#2a273f", "#e0def4", "#44415a",
             "#44415a", "#1f1d30", "#f6c177", "#eb6f92", "#44415a"),
)

_add(
    "everforest-dark", "Everforest Dark", True,
    "#2b3339", "#d3c6aa", "#d3c6aa", "#2b3339", "#4c555b", "#d3c6aa", "#a7c080",
    ("#2b3339", "#e67e80", "#a7c080", "#dbbc7f",
     "#7fbbb3", "#d699b6", "#83c092", "#d3c6aa",
     "#7a8478", "#e67e80", "#a7c080", "#dbbc7f",
     "#7fbbb3", "#d699b6", "#83c092", "#fdf6e3"),
    _surface("#2b3339", "#d3c6aa", "#232a2e", "#2b3339", "#232a2e", "#3a4248",
             "#859289", "#d3c6aa", "#232a2e", "#d3c6aa", "#4c555b",
             "#4c555b", "#232a2e", "#dbbc7f", "#e69875", "#4c555b"),
)

_add(
    "everforest-light", "Everforest Light", False,
    "#fdf6e3", "#5c6a72", "#5c6a72", "#fdf6e3", "#e0dcc7", "#5c6a72", "#8da101",
    ("#fdf6e3", "#f85552", "#8da101", "#dfa000",
     "#3a94c5", "#df69ba", "#35a77c", "#5c6a72",
     "#939f91", "#f85552", "#8da101", "#dfa000",
     "#3a94c5", "#df69ba", "#35a77c", "#5c6a72"),
    _surface("#fdf6e3", "#5c6a72", "#efebd4", "#fdf6e3", "#f4f0d9", "#efebd4",
             "#939f91", "#5c6a72", "#fffbef", "#5c6a72", "#e0dcc7",
             "#e0dcc7", "#f4f0d9", "#dfa000", "#f57d26", "#e0dcc7"),
)

_add(
    "kanagawa", "Kanagawa", True,
    "#1f1f28", "#dcd7ba", "#c8c093", "#1f1f28", "#2d4f67", "#dcd7ba", "#7e9cd8",
    ("#16161d", "#c34043", "#76946a", "#c0a36e",
     "#7e9cd8", "#957fb8", "#6a9589", "#c8c093",
     "#727169", "#e82424", "#98bb6c", "#e6c384",
     "#7fb4ca", "#938aa9", "#7aa89f", "#dcd7ba"),
    _surface("#1f1f28", "#dcd7ba", "#16161d", "#1f1f28", "#16161d", "#2a2a37",
             "#727169", "#dcd7ba", "#16161d", "#dcd7ba", "#2d4f67",
             "#2d4f67", "#16161d", "#e6c384", "#ffa066", "#2d4f67"),
)

_add(
    "monokai-pro", "Monokai Pro", True,
    "#2d2a2e", "#fcfcfa", "#ffd866", "#2d2a2e", "#5b595c", "#fcfcfa", "#ffd866",
    ("#403e41", "#ff6188", "#a9dc76", "#ffd866",
     "#fc9867", "#ab9df2", "#78dce8", "#fcfcfa",
     "#727072", "#ff6188", "#a9dc76", "#ffd866",
     "#fc9867", "#ab9df2", "#78dce8", "#fcfcfa"),
    _surface("#2d2a2e", "#fcfcfa", "#221f22", "#2d2a2e", "#221f22", "#403e41",
             "#939293", "#fcfcfa", "#221f22", "#fcfcfa", "#5b595c",
             "#5b595c", "#221f22", "#ffd866", "#fc9867", "#5b595c"),
)

_add(
    "iceberg-dark", "Iceberg Dark", True,
    "#161821", "#c6c8d1", "#e2a478", "#161821", "#2e313f", "#c6c8d1", "#84a0c6",
    ("#1e2132", "#e27878", "#b4be82", "#e2a478",
     "#84a0c6", "#a093c7", "#89b8c2", "#c6c8d1",
     "#6b7089", "#e27878", "#b4be82", "#e2a478",
     "#84a0c6", "#a093c7", "#89b8c2", "#c6c8d1"),
    _surface("#161821", "#c6c8d1", "#0f1118", "#161821", "#0f1118", "#1e2132",
             "#6b7089", "#c6c8d1", "#0f1118", "#c6c8d1", "#2e313f",
             "#2e313f", "#0f1118", "#e2a478", "#e27878", "#2e313f"),
)

_add(
    "iceberg-light", "Iceberg Light", False,
    "#e8e9ec", "#33374c", "#33374c", "#e8e9ec", "#cad0de", "#33374c", "#2d539e",
    ("#dcdfe7", "#cc517a", "#668e3d", "#c57339",
     "#2d539e", "#7759b4", "#3f83a6", "#33374c",
     "#8389a3", "#cc3768", "#598030", "#b6662d",
     "#22478e", "#6845ad", "#327698", "#262a3f"),
    _surface("#e8e9ec", "#33374c", "#d5d8e0", "#e8e9ec", "#dcdfe7", "#d5d8e0",
             "#8389a3", "#33374c", "#ffffff", "#33374c", "#cad0de",
             "#cad0de", "#dcdfe7", "#c57339", "#cc517a", "#cad0de"),
)

_add(
    "github-dark", "GitHub Dark", True,
    "#0d1117", "#c9d1d9", "#58a6ff", "#0d1117", "#264f78", "#c9d1d9", "#58a6ff",
    ("#484f58", "#ff7b72", "#3fb950", "#d29922",
     "#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4",
     "#6e7681", "#ffa198", "#56d364", "#e3b341",
     "#79c0ff", "#d2a8ff", "#56d4dd", "#f0f6fc"),
    _surface("#0d1117", "#c9d1d9", "#010409", "#0d1117", "#010409", "#161b22",
             "#8b949e", "#c9d1d9", "#161b22", "#c9d1d9", "#30363d",
             "#30363d", "#010409", "#d29922", "#f0883e", "#1f6feb"),
)

_add(
    "github-light", "GitHub Light", False,
    "#ffffff", "#24292f", "#0969da", "#ffffff", "#b6d8ff", "#24292f", "#0969da",
    ("#24292f", "#cf222e", "#116329", "#953800",
     "#0969da", "#8250df", "#1b7c83", "#6e7781",
     "#57606a", "#a40e26", "#1a7f37", "#9a6700",
     "#218bff", "#a475f9", "#3192aa", "#8c959f"),
    _surface("#ffffff", "#24292f", "#d0d7de", "#ffffff", "#f6f8fa", "#eaeef2",
             "#57606a", "#24292f", "#ffffff", "#24292f", "#d0d7de",
             "#d0d7de", "#f6f8fa", "#953800", "#bc4c00", "#ddf4ff"),
)

_add(
    "horizon", "Horizon", True,
    "#1c1e26", "#d5d8da", "#e95678", "#1c1e26", "#3d425b", "#d5d8da", "#e95678",
    ("#1c1e26", "#e95678", "#29d398", "#fab795",
     "#26bbd9", "#ee64ac", "#59e3e3", "#d5d8da",
     "#6c6f93", "#ec6a88", "#3fdaa4", "#fbc3a7",
     "#3fc4de", "#f075b5", "#6be4e6", "#e3e6e8"),
    _surface("#1c1e26", "#d5d8da", "#16171f", "#1c1e26", "#16171f", "#2e303e",
             "#6c6f93", "#d5d8da", "#16171f", "#d5d8da", "#3d425b",
             "#3d425b", "#16171f", "#fab795", "#e95678", "#3d425b"),
)

_add(
    "synthwave-84", "SynthWave '84", True,
    "#262335", "#f0eff1", "#f92aad", "#262335", "#463465", "#f0eff1", "#f92aad",
    ("#262335", "#fe4450", "#72f1b8", "#fede5d",
     "#2ee2fa", "#ff8b39", "#36f9f6", "#ffffff",
     "#848bbd", "#fe4450", "#72f1b8", "#fede5d",
     "#2ee2fa", "#ff8b39", "#36f9f6", "#ffffff"),
    _surface("#262335", "#f0eff1", "#1b1927", "#262335", "#1e1c2a", "#34294f",
             "#848bbd", "#f0eff1", "#1e1c2a", "#f0eff1", "#463465",
             "#463465", "#1b1927", "#fede5d", "#f92aad", "#463465"),
)

_add(
    "material-ocean", "Material Ocean", True,
    "#0f111a", "#a6accd", "#84ffff", "#0f111a", "#1f2233", "#a6accd", "#82aaff",
    ("#000000", "#f07178", "#c3e88d", "#ffcb6b",
     "#82aaff", "#c792ea", "#89ddff", "#ffffff",
     "#464b5d", "#f07178", "#c3e88d", "#ffcb6b",
     "#82aaff", "#c792ea", "#89ddff", "#ffffff"),
    _surface("#0f111a", "#a6accd", "#090b10", "#0f111a", "#090b10", "#1f2233",
             "#717cb4", "#a6accd", "#090b10", "#a6accd", "#1f2233",
             "#1f2233", "#090b10", "#ffcb6b", "#f78c6c", "#1f2233"),
)

_add(
    "dracula-light", "Dracula Light", False,
    "#f8f8f2", "#282a36", "#6272a4", "#f8f8f2", "#d7d7e0", "#282a36", "#7b5cd6",
    ("#21222c", "#c13145", "#2f8f4e", "#a06a12",
     "#3b5bdb", "#9a3fb5", "#0f7b8a", "#6b7280",
     "#6272a4", "#d94a5e", "#3aa663", "#c2831f",
     "#5573e8", "#b25fc9", "#1c93a3", "#282a36"),
    _surface("#f8f8f2", "#282a36", "#dcdce6", "#f8f8f2", "#efeff5", "#e4e4ec",
             "#6b7280", "#282a36", "#ffffff", "#282a36", "#d7d7e0",
             "#d7d7e0", "#efeff5", "#a06a12", "#c13145", "#e2e2ea"),
)

_add(
    "solarized-dark-hc", "Solarized Dark (高对比)", True,
    "#002b36", "#93a1a1", "#93a1a1", "#002b36", "#00505c", "#fdf6e3", "#268bd2",
    ("#073642", "#dc322f", "#859900", "#b58900",
     "#268bd2", "#d33682", "#2aa198", "#eee8d5",
     "#002b36", "#cb4b16", "#586e75", "#657b83",
     "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"),
    _surface("#002b36", "#93a1a1", "#001f27", "#002b36", "#00252e", "#073642",
             "#657b83", "#eee8d5", "#00252e", "#93a1a1", "#0b4453",
             "#0b4453", "#001c23", "#b58900", "#cb4b16", "#0b4453"),
)
