"""GTK CSS generation.

Everything the shell chrome looks like is produced here from the active theme,
so switching themes is a pure CSS swap and never needs a restart.
"""

from __future__ import annotations

from .theme import Theme


def build_css(theme: Theme) -> str:
    c = theme.ui

    def ui(key: str, fallback: str = "#000000") -> str:
        return c.get(key, theme.color(key, fallback))

    header_bg = ui("headerbar_bg", theme.background)
    header_fg = ui("headerbar_fg", theme.foreground)
    header_border = ui("headerbar_border", theme.background)
    tab_active_bg = ui("tab_active_bg", theme.background)
    tab_inactive_bg = ui("tab_inactive_bg", theme.background)
    tab_hover_bg = ui("tab_hover_bg", theme.background)
    tab_fg = ui("tab_fg", theme.foreground)
    tab_active_fg = ui("tab_active_fg", theme.foreground)
    status_bg = ui("statusbar_bg", theme.background)
    status_fg = ui("statusbar_fg", theme.foreground)
    border = ui("border", theme.background)
    overlay_bg = ui("overlay_bg", theme.background)
    overlay_fg = ui("overlay_fg", theme.foreground)
    overlay_border = ui("overlay_border", theme.foreground)
    search_bg = ui("search_bg", theme.background)
    scrollbar = ui("scrollbar", theme.foreground)
    split_border = ui("split_border", theme.background)
    accent = theme.accent
    dim_fg = _blend(tab_fg, theme.background, 0.25)

    return f"""
/* ---------- window shell ---------- */
window.vela {{
    background-color: {theme.background};
    color: {theme.foreground};
}}

headerbar.vela-headerbar {{
    background-image: none;
    background-color: {header_bg};
    color: {header_fg};
    border-bottom: 1px solid {header_border};
    box-shadow: none;
    min-height: 38px;
    padding: 0 4px;
}}

headerbar.vela-headerbar button {{
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
    color: {header_fg};
    min-height: 26px;
    min-width: 26px;
    padding: 2px 6px;
    border-radius: 6px;
}}

headerbar.vela-headerbar button:hover {{
    background-color: {tab_hover_bg};
}}

headerbar.vela-headerbar button:active,
headerbar.vela-headerbar button:checked {{
    background-color: {theme.selection};
}}

headerbar.vela-headerbar button.vela-active-toggle {{
    background-color: {theme.selection};
    color: {accent};
}}

headerbar.vela-headerbar label.title {{
    font-weight: 600;
}}

/* ---------- notebook / tabs ---------- */
notebook.vela-notebook > header {{
    background-color: {header_bg};
    border-color: {header_border};
    padding: 0;
}}

notebook.vela-notebook > header.top {{
    border-bottom: 1px solid {header_border};
}}

notebook.vela-notebook > header.bottom {{
    border-top: 1px solid {header_border};
}}

notebook.vela-notebook > header tab {{
    background-image: none;
    background-color: {tab_inactive_bg};
    border: none;
    border-radius: 8px 8px 0 0;
    color: {tab_fg};
    margin: 4px 2px 0 2px;
    padding: 5px 10px;
    min-height: 22px;
}}

notebook.vela-notebook > header.bottom tab {{
    border-radius: 0 0 8px 8px;
    margin: 0 2px 4px 2px;
}}

notebook.vela-notebook > header tab:hover {{
    background-color: {tab_hover_bg};
    color: {tab_active_fg};
}}

notebook.vela-notebook > header tab:checked {{
    background-color: {tab_active_bg};
    color: {tab_active_fg};
    box-shadow: inset 0 -2px 0 0 {accent};
}}

notebook.vela-notebook > header tab button.flat {{
    background: none;
    border: none;
    box-shadow: none;
    padding: 0;
    margin-left: 4px;
    min-width: 16px;
    min-height: 16px;
    color: {dim_fg};
}}

notebook.vela-notebook > header tab button.flat:hover {{
    color: {theme.palette.get("red", accent)};
    background-color: transparent;
}}

notebook.vela-notebook > stack {{
    background-color: {theme.background};
}}

/* ---------- terminal pane ---------- */
.vela-pane {{
    background-color: {theme.background};
    padding: 0;
}}

/* The focused-pane ring is painted by TerminalView itself: a box-shadow on a
   Gtk.Box is never drawn, because the widget paints no background of its own. */
.vela-pane.vela-bell {{
    box-shadow: inset 0 0 0 2px {_alpha(accent, 0.85)};
}}

paned.vela-paned > separator {{
    background-color: {split_border};
    min-width: 1px;
    min-height: 1px;
}}

paned.vela-paned > separator:hover {{
    background-color: {accent};
}}

/* ---------- status bar ---------- */
box.vela-statusbar {{
    background-color: {status_bg};
    color: {status_fg};
    border-top: 1px solid {border};
    padding: 2px 10px;
    font-size: 90%;
}}

box.vela-statusbar label {{
    color: {status_fg};
}}

box.vela-statusbar label.vela-status-accent {{
    color: {accent};
}}

/* ---------- search bar ---------- */
revealer.vela-search revealer box.vela-search-box,
box.vela-search-box {{
    background-color: {search_bg};
    border: 1px solid {overlay_border};
    border-radius: 10px;
    padding: 6px 8px;
    box-shadow: 0 6px 18px {_alpha("#000000", 0.35 if theme.dark else 0.16)};
}}

box.vela-search-box entry {{
    background-color: {tab_inactive_bg};
    border: 1px solid {border};
    border-radius: 6px;
    color: {theme.foreground};
    caret-color: {accent};
    padding: 3px 6px;
}}

box.vela-search-box entry:focus {{
    border-color: {accent};
}}

box.vela-search-box label.vela-search-count {{
    color: {dim_fg};
    font-size: 90%;
}}

button.vela-icon-button {{
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
    color: {overlay_fg};
    border-radius: 6px;
    min-width: 26px;
    min-height: 26px;
    padding: 2px;
}}

button.vela-icon-button:hover {{
    background-color: {tab_hover_bg};
}}

button.vela-icon-button:checked {{
    background-color: {theme.selection};
    color: {tab_active_fg};
}}

/* ---------- command palette ---------- */
box.vela-palette {{
    background-color: {overlay_bg};
    border: 1px solid {overlay_border};
    border-radius: 12px;
    padding: 8px;
    box-shadow: 0 16px 40px {_alpha("#000000", 0.45 if theme.dark else 0.2)};
}}

box.vela-palette entry.vela-palette-entry {{
    background-color: {tab_inactive_bg};
    border: 1px solid {border};
    border-radius: 8px;
    padding: 6px 8px;
    font-size: 105%;
    color: {theme.foreground};
    caret-color: {accent};
}}

box.vela-palette entry.vela-palette-entry:focus {{
    border-color: {accent};
}}

box.vela-palette scrolledwindow {{
    background: none;
    border: none;
}}

box.vela-palette treeview,
box.vela-palette treeview.view {{
    background-color: {overlay_bg};
    color: {overlay_fg};
}}

box.vela-palette treeview:selected,
box.vela-palette treeview.view:selected {{
    background-color: {theme.selection};
    color: {tab_active_fg};
}}

box.vela-palette treeview.view cell {{
    border-color: {overlay_bg};
}}

box.vela-palette treeview.view:selected cell {{
    border-color: {theme.selection};
}}

list.vela-palette-list {{
    background: none;
    color: {overlay_fg};
}}

list.vela-palette-list row {{
    border-radius: 6px;
    padding: 4px 6px;
}}

list.vela-palette-list row:selected {{
    background-color: {theme.selection};
    color: {tab_active_fg};
}}

list.vela-palette-list row label.vela-palette-hint {{
    color: {dim_fg};
    font-size: 88%;
}}

list.vela-palette-list row:selected label.vela-palette-hint {{
    color: {tab_active_fg};
}}

/* ---------- sysinfo panel ---------- */
box.vela-sysinfo {{
    background-color: {overlay_bg};
    border-left: 1px solid {border};
    border-right: 1px solid {border};
}}

box.vela-sysinfo label.vela-panel-title {{
    font-weight: 600;
    color: {overlay_fg};
    padding: 8px 10px 0 10px;
}}

box.vela-sysinfo label.vela-panel-section {{
    font-weight: 600;
    font-size: 88%;
    color: {accent};
    margin-top: 4px;
}}

box.vela-sysinfo progressbar.vela-meter trough {{
    background-color: {tab_inactive_bg};
    border: none;
    border-radius: 999px;
    min-height: 8px;
}}

box.vela-sysinfo progressbar.vela-meter progress {{
    background-image: none;
    background-color: {accent};
    border-radius: 999px;
    min-height: 8px;
}}

box.vela-sysinfo progressbar.vela-meter-warn progress {{
    background-color: {theme.palette.get("yellow", accent)};
}}

box.vela-sysinfo progressbar.vela-meter-crit progress {{
    background-color: {theme.palette.get("red", accent)};
}}

box.vela-sysinfo progressbar.vela-meter text {{
    color: {overlay_fg};
    font-size: 80%;
}}

/* ---------- file viewer ---------- */
/* ---------- file browser panel ---------- */
box.vela-filebrowser {{
    background-color: {overlay_bg};
    border-left: 1px solid {border};
    border-right: 1px solid {border};
}}

box.vela-filebrowser label.vela-panel-title {{
    font-weight: 600;
    color: {overlay_fg};
    padding: 8px 10px 0 10px;
}}

scrolledwindow.vela-crumbs {{
    background-color: {tab_inactive_bg};
    border-top: 1px solid {border};
    border-bottom: 1px solid {border};
}}

button.vela-crumb {{
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
    color: {overlay_fg};
    padding: 2px 4px;
    min-height: 20px;
}}

button.vela-crumb:hover {{
    background-color: {tab_hover_bg};
    border-radius: 4px;
}}

treeview.vela-filelist {{
    background-color: {overlay_bg};
    color: {overlay_fg};
}}

treeview.vela-filelist:selected,
treeview.vela-filelist.view:selected {{
    background-color: {theme.selection};
    color: {tab_active_fg};
}}

treeview.vela-filelist.view cell {{
    border-color: {overlay_bg};
    padding: 1px 4px;
}}

treeview.vela-filelist.view:selected cell {{
    border-color: {theme.selection};
}}

treeview.vela-filelist header button {{
    background-image: none;
    background-color: {tab_inactive_bg};
    border: none;
    box-shadow: none;
    color: {tab_fg};
    padding: 3px 6px;
}}

treeview.vela-filelist header button:hover {{
    background-color: {tab_hover_bg};
    color: {tab_active_fg};
}}

box.vela-filebrowser label.vela-error {{
    color: {theme.palette.get("red", accent)};
}}

box.vela-command-bar {{
    background-color: {tab_inactive_bg};
    border-top: 1px solid {border};
}}

box.vela-command-bar label.vela-command-prompt {{
    color: {accent};
    font-family: monospace;
    font-weight: 600;
    padding: 0 2px;
}}

box.vela-command-bar entry.vela-command-entry {{
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
    color: {overlay_fg};
    caret-color: {accent};
    font-family: monospace;
    min-height: 22px;
}}

box.vela-command-bar entry.vela-command-entry:focus {{
    box-shadow: none;
}}

window.vela textview.vela-source,
window.vela textview.vela-source text {{
    background-color: {theme.background};
    color: {theme.foreground};
}}

window.vela image.vela-image {{
    background-color: {theme.background};
}}

dialog.vela-dialog {{
    background-color: {overlay_bg};
    color: {overlay_fg};
}}

dialog.vela-dialog headerbar {{
    background-color: {header_bg};
    color: {header_fg};
}}

dialog.vela-dialog .vela-section-title {{
    font-weight: 600;
    color: {accent};
    margin-top: 6px;
}}

/* ---------- misc ---------- */
scrolledwindow.vela-scroll scrollbar slider {{
    background-color: {scrollbar};
    border-radius: 999px;
    min-width: 6px;
    min-height: 6px;
}}

scrolledwindow.vela-scroll scrollbar slider:hover {{
    background-color: {accent};
}}

.vela-dim {{
    color: {dim_fg};
}}

.vela-error {{
    color: {theme.palette.get("red", accent)};
}}

box.vela-statusbar label.vela-error {{
    color: {theme.palette.get("red", accent)};
}}

box.vela-statusbar label.vela-warning {{
    color: {theme.palette.get("yellow", accent)};
}}

tooltip.background {{
    background-color: {overlay_bg};
    color: {overlay_fg};
    border: 1px solid {overlay_border};
    border-radius: 6px;
}}
"""


def _alpha(color: str, factor: float) -> str:
    """``#rrggbb`` + alpha factor -> ``rgba(r, g, b, a)``."""
    red, green, blue = _rgb(color)
    return f"rgba({red}, {green}, {blue}, {factor:.3f})"


def _rgb(color: str) -> tuple:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return (0, 0, 0)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _blend(color: str, background: str, amount: float) -> str:
    """Mix ``color`` towards ``background`` by ``amount`` (0..1)."""
    fg = _rgb(color)
    bg = _rgb(background)
    mixed = tuple(
        int(round(fg[index] * (1 - amount) + bg[index] * amount)) for index in range(3)
    )
    return "#{:02x}{:02x}{:02x}".format(*mixed)
