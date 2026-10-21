from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def find_opera_gx() -> Path | None:
    """Szuka Opera GX — preferuje działający profil użytkownika."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    candidates = [
        local / "Programs" / "Opera GX" / "opera.exe",
        local / "Programs" / "Opera GX" / "launcher.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Opera GX"
        / "opera.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Opera GX"
        / "opera.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def open_url_in_opera_gx(url: str) -> bool:
    """
    Otwiera URL w Opera GX.
    Gdy Opera już działa, zwykle dokłada zakładkę do istniejącego okna
    (bez --new-window).
    """
    opera = find_opera_gx()
    if opera is None:
        return False
    # Bez --new-window: działająca Opera GX zwykle dokłada zakładkę.
    try:
        subprocess.Popen(
            [str(opera), url],
            cwd=str(opera.parent),
            close_fds=True,
        )
    except OSError:
        return False
    return True


def open_web_url(url: str) -> bool:
    """Najpierw Opera GX (istniejąca sesja), inaczej domyślna przeglądarka."""
    if open_url_in_opera_gx(url):
        return True
    return QDesktopServices.openUrl(QUrl(url))
