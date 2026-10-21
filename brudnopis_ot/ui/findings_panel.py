from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from brudnopis_ot.core.models import Finding, ValidationResult

_SEVERITY_COLORS = {
    "ok": "#128c7e",
    "info": "#0b3d4a",
    "warning": "#be123c",
    "violation": "#e11d48",
}


class FindingsPanel(QWidget):
    finding_activated = Signal(object)  # Finding

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_click)
        self._hints = QLabel()
        self._hints.setWordWrap(True)
        self._hints.setStyleSheet("color: #5a6672; font-size: 12px;")

        title = QLabel(_tr("Zgodność OT + jazda 561"))
        font = QFont()
        font.setBold(True)
        title.setFont(font)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self._list, stretch=1)
        layout.addWidget(QLabel(_tr("Podpowiedzi (OT / 10h / między OT / PM)")))
        layout.addWidget(self._hints)
        self.setMinimumWidth(280)

    def set_result(self, result: ValidationResult | None) -> None:
        self._list.clear()
        if result is None:
            self._hints.setText("")
            return
        for finding in result.findings:
            item = QListWidgetItem(finding.message_pl)
            item.setData(Qt.ItemDataRole.UserRole, finding)
            color = _SEVERITY_COLORS.get(finding.severity, "#111827")
            item.setForeground(QColor(color))
            prefix = {
                "ok": "OK · ",
                "info": "ℹ · ",
                "warning": "⚠ · ",
                "violation": "✗ · ",
            }.get(finding.severity, "")
            item.setText(prefix + finding.message_pl)
            self._list.addItem(item)
        hints = list(result.compensation_hints)
        self._hints.setText("\n".join(hints) if hints else "—")

    def _on_click(self, item: QListWidgetItem) -> None:
        finding = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(finding, Finding):
            self.finding_activated.emit(finding)
