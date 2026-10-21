from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

class KpiItem(QWidget):
    """Jedna para etykieta + wartość mono (bez karty)."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._title = QLabel(title)
        self._title.setObjectName("kpiTitle")
        self._value = QLabel("—")
        self._value.setObjectName("kpiValue")
        self._value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self._title)
        row.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text or "—")

class KpiStrip(QFrame):
    """Pasek wartości KPI — inline, bez ramek kart."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("kpiStrip")
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(0)
        self.norma = KpiItem(_tr("Norma"))
        self.work = KpiItem(_tr("Czas pracy"))
        self.wn_open = KpiItem(_tr("Wn otwarte"))
        self.milo = KpiItem("MiLo top-up")
        self.gross = KpiItem(_tr("Brutto"))
        self.net = KpiItem(_tr("Netto≈"))
        items = (
            self.norma,
            self.work,
            self.wn_open,
            self.milo,
            self.gross,
            self.net,
        )
        for i, item in enumerate(items):
            if i > 0:
                sep = QLabel("│")
                sep.setObjectName("kpiSep")
                row.addWidget(sep)
            row.addWidget(item)
            row.addSpacing(8)
        row.addStretch(1)

    def clear(self) -> None:
        for item in (
            self.norma,
            self.work,
            self.wn_open,
            self.milo,
            self.gross,
            self.net,
        ):
            item.set_value("—")

    def update_values(
        self,
        *,
        norma: str,
        work: str,
        wn_open: str,
        milo: str,
        gross: str,
        net: str,
    ) -> None:
        self.norma.set_value(norma)
        self.work.set_value(work)
        self.wn_open.set_value(wn_open)
        self.milo.set_value(milo)
        self.gross.set_value(gross)
        self.net.set_value(net)
