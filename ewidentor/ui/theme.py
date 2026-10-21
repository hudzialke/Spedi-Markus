"""Kolory motywu Spedi / MarkusDispatcher."""

from __future__ import annotations

BG_APP = "#e9eef2"
BG_PANEL = "#ffffff"
BG_ELEVATED = "#ffffff"
BG_MUTED = "#f7f9fb"
BG_CHROME = "#e9eef2"
BG_CHROME_MUTED = "#d0d7de"
BG_HEADER_BAND = "#0b3d4a"
BG_BUTTON = "#dbe2e8"
BG_BUTTON_HOVER = "#cdd5dc"

TEXT_PRIMARY = "#101820"
TEXT_SECONDARY = "#5a6672"
TEXT_MUTED = "#5a6672"
TEXT_ON_DARK = "#ffffff"
TEXT_ON_ACCENT = "#ffffff"
TEXT_ON_CHROME = "#101820"

BRAND = "#0b3d4a"
ACCENT = "#0b3d4a"
ACCENT_HOVER = "#082e38"
ACCENT_TINT = "#eef6f8"
OK = "#0b3d4a"
FOCUS_RING = "#0b3d4a"

BORDER = "#d0d7de"
BORDER_STRONG = "#c5ced6"
GRIDLINE = "#d0d7de"
ROW_ALT = "#f7f9fb"
SELECTION = "#eef6f8"
SELECTION_TEXT = "#101820"

WARN = "#be123c"
DANGER = "#e11d48"
INFO_MANUAL = "#0b3d4a"
# Sugestia typu MiLo (nie CTA, nie brand) — hint, nie zapis.
SUGGEST = "#128c7e"

ABSENCE_BG = "#eef6f8"
WN_BG = "#fde8ee"
MANUAL_BG = "#eef6f8"
NO_COUNTRY_BG = "#f7f9fb"

HEAT_LOW = "#eef6f8"
HEAT_HIGH = "#0b3d4a"

SPACE_UNIT = 4
ROW_HEIGHT = 26
RADIUS_SM = 0
RADIUS_MD = 0


def lerp_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    r = int(ar + (br - ar) * t)
    g = int(ag + (bg - ag) * t)
    bl = int(ab + (bb - ab) * t)
    return f"#{r:02X}{g:02X}{bl:02X}"


def heat_color(t: float) -> str:
    return lerp_hex(HEAT_LOW, HEAT_HIGH, t)
