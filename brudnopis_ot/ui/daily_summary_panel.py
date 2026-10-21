from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from brudnopis_ot.core.daily_summary import DayTotals, format_minutes, sum_totals


class DailySummaryPanel(QWidget):
    """Tabela sum dziennych (jak w DDD Analyzer) z bloków brudnopisu."""

    day_activated = Signal(object)  # date

    HEADERS = [_tr("Dzień"), _tr("Jazda"), _tr("Praca"), _tr("Dyspozycyjność"), _tr("Odpoczynek")]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        title = QLabel(_tr("Podsumowanie dni"))
        font = QFont()
        font.setBold(True)
        title.setFont(font)

        totals_layout = QHBoxLayout()
        self.drive_label = QLabel(_tr("Jazda: —"))
        self.work_label = QLabel(_tr("Praca: —"))
        self.avail_label = QLabel(_tr("Dyspozycyjność: —"))
        self.rest_label = QLabel(_tr("Odpoczynek: —"))
        for label in (self.drive_label, self.work_label, self.avail_label, self.rest_label):
            label.setStyleSheet("font-size: 12px;")
            totals_layout.addWidget(label)
        totals_layout.addStretch()

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._emit_day)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title)
        layout.addLayout(totals_layout)
        layout.addWidget(self.table, stretch=1)
        self.setMinimumWidth(280)
        self.setMinimumHeight(220)

    def set_days(self, days: list[DayTotals]) -> None:
        self.table.setRowCount(len(days))
        for row, summary in enumerate(days):
            values = [
                summary.day.strftime("%d.%m.%Y"),
                format_minutes(summary.drive_min),
                format_minutes(summary.work_min),
                format_minutes(summary.availability_min),
                format_minutes(summary.rest_min),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, summary.day)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

        if days:
            week = sum_totals(days)
            self.drive_label.setText(f"{_tr('Jazda: ')}{format_minutes(week.drive_min)}")
            self.work_label.setText(f"{_tr('Praca: ')}{format_minutes(week.work_min)}")
            self.avail_label.setText(f"{_tr('Dyspozycyjność: ')}{format_minutes(week.availability_min)}")
            self.rest_label.setText(f"{_tr('Odpoczynek: ')}{format_minutes(week.rest_min)}")
        else:
            self.drive_label.setText(_tr("Jazda: —"))
            self.work_label.setText(_tr("Praca: —"))
            self.avail_label.setText(_tr("Dyspozycyjność: —"))
            self.rest_label.setText(_tr("Odpoczynek: —"))

    def _emit_day(self) -> None:
        day = self.selected_day()
        if day is not None:
            self.day_activated.emit(day)

    def selected_day(self) -> date | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        stored = item.data(Qt.ItemDataRole.UserRole)
        return stored if isinstance(stored, date) else None
