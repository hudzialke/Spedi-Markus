from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date, timedelta

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from brudnopis_ot.core.models import Scenario


def default_report_dates(
    scenario: Scenario,
    *,
    profile_from: date | None = None,
    profile_to: date | None = None,
) -> tuple[date, date]:
    """Domyślny okres: bloki scenariusza, inaczej zakres profilu DDD, inaczej tydzień scenariusza."""
    if scenario.blocks:
        start = min(b.start.date() for b in scenario.blocks)
        end = max((b.end - timedelta(seconds=1)).date() for b in scenario.blocks)
        return start, end
    if profile_from and profile_to:
        return profile_from, profile_to
    start = scenario.week_start
    end = start + timedelta(days=7 * max(1, scenario.weeks) - 1)
    return start, end


class ReportPeriodDialog(QDialog):
    """Wybór okresu Od–Do dla raportu PDF."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        profile_from: date | None = None,
        profile_to: date | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Raport PDF — okres"))
        self.resize(360, 160)

        start, end = default_report_dates(
            scenario,
            profile_from=profile_from,
            profile_to=profile_to,
        )

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel(_tr("Wybierz okres raportu (walidacja liczy się dla całego scenariusza):"))
        )

        form = QFormLayout()
        self._from = QDateEdit()
        self._from.setCalendarPopup(True)
        self._from.setDisplayFormat("dd.MM.yyyy")
        self._from.setDate(QDate(start.year, start.month, start.day))
        self._to = QDateEdit()
        self._to.setCalendarPopup(True)
        self._to.setDisplayFormat("dd.MM.yyyy")
        self._to.setDate(QDate(end.year, end.month, end.day))
        form.addRow(_tr("Od:"), self._from)
        form.addRow(_tr("Do:"), self._to)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_tr("Generuj"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        start = self.selected_start()
        end = self.selected_end()
        if end < start:
            QMessageBox.warning(
                self,
                _tr("Okres"),
                _tr("Data końcowa nie może być wcześniejsza niż początkowa."),
            )
            return
        self.accept()

    def selected_start(self) -> date:
        qd = self._from.date()
        return date(qd.year(), qd.month(), qd.day())

    def selected_end(self) -> date:
        qd = self._to.date()
        return date(qd.year(), qd.month(), qd.day())
