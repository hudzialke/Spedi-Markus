from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QWidget


def as_embedded_widget(window: QMainWindow) -> QWidget:
    """Osadź istniejące QMainWindow jako zakładkę (toolbar zostaje wewnątrz)."""
    window.setWindowFlags(Qt.WindowType.Widget)
    window.setContentsMargins(0, 0, 0, 0)
    return window
