from __future__ import annotations

from spedi.i18n import tr as _tr
from datetime import date
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)
class DayInspector(QFrame):
    """Skrót wybranego dnia — płaski panel (prawa kolumna, góra)."""
    edit_requested = Signal()
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("inspectorFrame")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._title = QLabel(_tr("Inspektor dnia"))
        self._title.setStyleSheet("font-weight: 600; font-size: 13px;")
        head.addWidget(self._title, 1)
        self._edit_btn = QPushButton(_tr("Edytuj dzień"))
        self._edit_btn.setObjectName("primaryButton")
        self._edit_btn.clicked.connect(self.edit_requested.emit)
        self._edit_btn.setEnabled(False)
        head.addWidget(self._edit_btn)
        lay.addLayout(head)
        self._body = QLabel(_tr("Wybierz dzień na liście lub w kalendarzu."))
        self._body.setObjectName("sectionHint")
        self._body.setWordWrap(True)
        lay.addWidget(self._body)
        self._day: date | None = None
    @property
    def selected_day(self) -> date | None:
        return self._day
    def clear(self) -> None:
        self._day = None
        self._title.setText(_tr("Inspektor dnia"))
        self._body.setText(_tr("Wybierz dzień na liście lub w kalendarzu."))
        self._edit_btn.setEnabled(False)
    def show_day(
        self,
        day: date,
        *,
        weekday_pl: str,
        countries: str,
        start: str,
        end: str,
        work: str,
        drive: str,
        duty: str,
        night: str,
        overtime: str,
        absence: str,
        badges: list[str],
        blocks_summary: str,
    ) -> None:
        self._day = day
        self._title.setText(f"{day.strftime('%d.%m.%Y')} ({weekday_pl})")
        badge_txt = ", ".join(badges) if badges else "—"
        lines = [
            f"<b>Kraje:</b> {countries or '—'}",
            f"<b>Start / koniec:</b> {start} – {end}",
            f"<b>Praca:</b> {work} · <b>Jazda:</b> {drive}",
            f"{_tr('<b>Dyżur:</b> ')}{duty} · <b>Noc:</b> {night} · <b>Nadg.:</b> {overtime}",
            f"{_tr('<b>Nieobecność:</b> ')}{absence or '—'}",
            f"<b>Znaczniki:</b> {badge_txt}",
            f"<b>Bloki:</b> {blocks_summary or 'brak danych'}",
        ]
        self._body.setText("<br/>".join(lines))
        self._edit_btn.setEnabled(True)
