from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from brudnopis_ot.core.models import (
    ACTIVITY_COLORS,
    ACTIVITY_LABELS_PL,
    COMPENSATION_BAR_COLOR,
    ActivityKind,
)


class _Swatch(QWidget):
    def __init__(
        self,
        color: str,
        *,
        striped: bool = False,
        overlay_bottom: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._striped = striped
        self._overlay_bottom = QColor(overlay_bottom) if overlay_bottom else None
        self.setFixedSize(22, 14)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.fillRect(self.rect(), self._color)
        if self._striped:
            p.setPen(QColor("#9CA3AF"))
            for x in range(-10, self.width() + 10, 4):
                p.drawLine(x, self.height(), x + self.height(), 0)
        if self._overlay_bottom is not None:
            h = 5
            p.fillRect(0, self.height() - h, self.width(), h, self._overlay_bottom)
        p.setPen(QColor("#6B7280"))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)


class LegendWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        act_row = QHBoxLayout()
        act_row.addWidget(QLabel(_tr("<b>Aktywności</b>")))
        for kind in ActivityKind:
            act_row.addWidget(
                _Swatch(ACTIVITY_COLORS[kind], striped=(kind == ActivityKind.CARD_OUT))
            )
            act_row.addWidget(QLabel(ACTIVITY_LABELS_PL[kind]))
        act_row.addStretch()
        root.addLayout(act_row)

        ot_row = QHBoxLayout()
        ot_row.addWidget(QLabel(_tr("<b>Odpoczynki</b>")))
        ot_row.addWidget(QLabel(_tr("h(45) regularny OT")))
        ot_row.addWidget(QLabel("·"))
        ot_row.addWidget(QLabel(_tr("h(24) skrócony OT")))
        ot_row.addWidget(QLabel("·"))
        ot_row.addWidget(
            _Swatch(ACTIVITY_COLORS[ActivityKind.REST], overlay_bottom=COMPENSATION_BAR_COLOR)
        )
        ot_row.addWidget(QLabel(_tr("rekompensata zaliczona (zielony pasek)")))
        ot_row.addStretch()
        root.addLayout(ot_row)

        viol_row = QHBoxLayout()
        viol_row.addWidget(QLabel(_tr("<b>Naruszenia / terminy</b>")))
        viol_row.addWidget(_Swatch("#EA580C"))
        viol_row.addWidget(QLabel(_tr("pomarańczowa linia: najpóźniejszy start rekompensat")))
        viol_row.addWidget(QLabel("·"))
        viol_row.addWidget(_Swatch("#DC2626"))
        viol_row.addWidget(QLabel(_tr("czerwona linia: start regularnego OT — 6×24h")))
        viol_row.addWidget(QLabel("·"))
        viol_row.addWidget(QLabel(_tr("Jazda: 10h×2 / 56h / 90h")))
        viol_row.addWidget(QLabel("·"))
        viol_row.addWidget(QLabel(_tr("OD/dobowe 9–<11h: max 3× między OT")))
        viol_row.addStretch()
        root.addLayout(viol_row)
