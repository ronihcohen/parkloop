"""Omarchy theme integration for ParkLoop.

Loads the active Omarchy theme from ``~/.local/state/omarchy/current/theme/colors.toml``
(with fallbacks) and builds a Qt stylesheet + map palette so the entire app
— main window, sidebar, dialogs/modals, tables, menus — matches the system theme.
"""
from __future__ import annotations

import os
from pathlib import Path

# Built-in fallback: Omarchy "ethereal" (dark).
FALLBACK_COLORS = {
    "mode": "dark",
    "accent": "#7d82d9",
    "selection": "#252e56",
    "muted": "#6d7db6",
    "background": "#060B1E",
    "dark_background": "#040816",
    "darker_background": "#030610",
    "lighter_background": "#131a3a",
    "foreground": "#ffcead",
    "dark_foreground": "#6d7db6",
    "light_foreground": "#c9b8a6",
    "bright_foreground": "#ffcead",
    "red": "#ED5B5A",
    "yellow": "#E9BB4F",
    "orange": "#eb8b54",
    "green": "#92a593",
    "cyan": "#a3bfd1",
    "blue": "#7d82d9",
    "magenta": "#c89dc1",
    "brown": "#75452a",
}

_CANDIDATES = (
    Path.home() / ".local/state/omarchy/current/theme/colors.toml",
    Path("/usr/share/omarchy/themes/ethereal/colors.toml"),
)


def _parse_simple_toml(text: str) -> dict:
    """Minimal ``key = \"value\"`` parser (avoids a tomllib dependency quirk)."""
    data: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Skip section headers like [colors] if ever present.
        if key.startswith("["):
            continue
        data[key] = value
    return data


def load_omarchy_colors(path: Path | None = None) -> dict:
    """Return active Omarchy colors, falling back to built-in ethereal."""
    colors = dict(FALLBACK_COLORS)
    candidates = [Path(path)] if path else []
    env_override = os.environ.get("PARKLOOP_THEME_COLORS")
    if env_override:
        candidates.append(Path(env_override))
    candidates.extend(_CANDIDATES)
    for candidate in candidates:
        try:
            if candidate.is_file():
                parsed = _parse_simple_toml(candidate.read_text(encoding="utf-8"))
                colors.update({k: v for k, v in parsed.items() if v})
                return colors
        except OSError:
            continue
    return colors


def build_stylesheet(colors: dict | None = None) -> str:
    """Build the application-wide Qt stylesheet from Omarchy colors."""
    c = dict(FALLBACK_COLORS)
    if colors:
        c.update(colors)
    bg = c["background"]
    bg_dark = c.get("dark_background", c["background"])
    bg_darker = c.get("darker_background", bg_dark)
    bg_light = c.get("lighter_background", c["background"])
    fg = c["foreground"]
    fg_dim = c.get("light_foreground", fg)
    muted = c["muted"]
    accent = c["accent"]
    sel = c["selection"]
    green = c.get("green", accent)
    red = c.get("red", "#ED5B5A")

    return f"""
QWidget {{ font-family: "DejaVu Sans", "Segoe UI", sans-serif; font-size: 12px; color: {fg}; background: {bg}; }}
QMainWindow, QDialog, QMessageBox, QFileDialog {{ background: {bg}; color: {fg}; }}
QFrame#sidebar {{ background: {bg_dark}; border-right: 1px solid {sel}; }}
QFrame#sidebarFooter {{ background: {bg_dark}; border-top: 1px solid {sel}; }}
QLabel#muted {{ color: {muted}; font-size: 11px; }}
QPushButton#disclosure {{ text-align: left; border: 0; background: transparent; padding: 7px 0; color: {fg_dim}; }}
QPushButton#disclosure:hover {{ color: {accent}; }}
QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 28px; font-weight: 700; color: {fg}; background: transparent; }}
QLabel#eyebrow {{ font-size: 10px; color: {muted}; letter-spacing: 1px; background: transparent; }}
QLabel#distance {{ font-size: 38px; font-weight: 600; color: {fg}; background: transparent; }}
QPushButton, QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{
  padding: 9px; border: 1px solid {sel}; border-radius: 7px; background: {bg_light}; color: {fg};
}}
QPushButton:hover, QComboBox:hover {{ background: {sel}; }}
QPushButton#primary {{ background: {accent}; color: {bg_darker}; border: 0; font-weight: 600; padding: 12px; }}
QPushButton#primary:hover {{ background: {green}; color: {bg_darker}; }}
QPushButton#primary:disabled {{ background: {sel}; color: {muted}; }}
QPushButton:disabled {{ color: {muted}; background: {bg_dark}; border-color: {sel}; }}
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled {{ color: {muted}; }}
QComboBox QAbstractItemView {{ background: {bg_light}; color: {fg}; selection-background-color: {accent}; selection-color: {bg_darker}; outline: 0; }}
QComboBox QAbstractItemView::item {{ padding: 6px; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{ background: {accent}; color: {bg_darker}; }}
QProgressBar {{ max-height: 6px; border: 0; background: {sel}; color: {fg}; }}
QProgressBar::chunk {{ background: {accent}; }}
QMenuBar, QMenu {{ background: {bg_dark}; color: {fg}; }}
QMenu::item:selected {{ background: {accent}; color: {bg_darker}; }}
QScrollArea {{ background: {bg_dark}; border: 0; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: {bg_dark}; border: 0; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {sel}; border-radius: 4px; min-height: 20px; }}
QScrollBar::handle:hover {{ background: {accent}; }}
QDialogButtonBox QPushButton {{ padding: 9px 16px; }}
/* Alternatives side-panel list */
QListWidget {{ background: {bg_dark}; color: {fg}; border: 1px solid {sel}; border-radius: 7px; padding: 4px; outline: 0; }}
QListWidget::item {{ padding: 8px; border-radius: 5px; }}
QListWidget::item:selected {{ background: {accent}; color: {bg_darker}; }}
QListWidget::item:hover:!selected {{ background: {sel}; }}
/* Modal / compare dialog table */
QTableWidget {{ background: {bg_dark}; alternate-background-color: {bg_light}; color: {fg}; gridline-color: {sel}; selection-background-color: {accent}; selection-color: {bg_darker}; border: 1px solid {sel}; }}
QTableWidget::item {{ padding: 4px; }}
QTableWidget::item:selected {{ background: {accent}; color: {bg_darker}; }}
QHeaderView::section {{ background: {bg_light}; color: {fg_dim}; border: 0; border-bottom: 1px solid {sel}; padding: 6px; }}
QToolTip {{ background: {bg_light}; color: {fg}; border: 1px solid {sel}; }}
QMessageBox QLabel {{ color: {fg}; }}
"""


def map_palette(colors: dict | None = None, mode: str = "light") -> dict:
    """Colors used by MapView so the map chrome matches the theme.

    ``mode`` is ``"light"`` (default) or ``"dark"`` and controls the map
    tile backdrop + chrome. Route/reference/start accents always come from
    the Omarchy theme so the route stays recognizable in both modes.
    """
    c = dict(FALLBACK_COLORS)
    if colors:
        c.update(colors)
    if str(mode).lower() == "light":
        return {
            "background": "#e9ede5",
            "grid": "#d6ded1",
            "route": c.get("accent", c["blue"]),
            "reference": c.get("magenta", c["accent"]),
            "start": c.get("orange", c["accent"]),
            "handle_fill": "#ffffff",
            "attribution_bg": "#ffffff",
            "attribution_fg": "#344139",
            "notice_bg": "#ffffff",
            "notice_fg": "#344139",
            # No dimming overlay in light mode; tiles are drawn as-is.
            "tile_dim": 0,
        }
    return {
        "background": c["background"],
        "grid": c["muted"],
        "route": c["accent"],
        "reference": c.get("magenta", c["accent"]),
        "start": c.get("orange", c["accent"]),
        "handle_fill": "#ffffff",
        "attribution_bg": c["background"],
        "attribution_fg": c["foreground"],
        "notice_bg": c.get("lighter_background", c["background"]),
        "notice_fg": c["foreground"],
        # Alpha of the dark tint painted over (light) OSM tiles in dark mode.
        "tile_dim": 110,
    }
