from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication


def _resource_file(name: str) -> Path | None:
    pkg = resources.files("markus_dispatcher").joinpath("resources", name)
    candidate = Path(str(pkg))
    if candidate.is_file():
        return candidate
    if getattr(sys, "frozen", False):
        roots = [
            Path(sys.executable).resolve().parent,
            Path(getattr(sys, "_MEIPASS", "")),
        ]
        for root in roots:
            if not root:
                continue
            p = root / "markus_dispatcher" / "resources" / name
            if p.is_file():
                return p
    return None


def app_icon() -> QIcon:
    icon = QIcon()
    ico = _resource_file("app.ico")
    if ico is not None:
        icon.addFile(str(ico))
    png = _resource_file("app.png")
    if png is not None:
        icon.addFile(str(png))
    if icon.isNull() and png is not None:
        pix = QPixmap(str(png))
        if not pix.isNull():
            icon.addPixmap(pix)
    return icon


def apply_theme(app: QApplication) -> None:
    qss = resources.files("markus_dispatcher").joinpath("resources/app.qss")
    app.setStyleSheet(qss.read_text(encoding="utf-8"))
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
