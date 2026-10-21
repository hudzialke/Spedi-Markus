from __future__ import annotations

from importlib import resources
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


def app_icon() -> QIcon:
    icon = QIcon()
    ico = resources.files("spedi").joinpath("ui/app.ico")
    candidate = Path(str(ico))
    if candidate.is_file():
        icon.addFile(str(candidate))
    return icon


def apply_theme(app: QApplication) -> None:
    qss = resources.files("spedi").joinpath("ui/app.qss")
    app.setStyleSheet(qss.read_text(encoding="utf-8"))
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
