from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ddd_analyzer.models import DailySummary, WeekSummary
from ddd_analyzer.summarize import format_minutes


class SummaryPanel(QWidget):
    HEADERS = [_tr("Dzien"), _tr("Jazda"), _tr("Praca"), _tr("Dyspozycyjnosc"), _tr("Odpoczynek"), _tr("Odometr km")]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        totals_layout = QHBoxLayout()
        self.drive_label = QLabel(_tr("Jazda: —"))
        self.work_label = QLabel(_tr("Praca: —"))
        self.avail_label = QLabel(_tr("Dyspozycyjnosc: —"))
        self.rest_label = QLabel(_tr("Odpoczynek: —"))
        for label in (self.drive_label, self.work_label, self.avail_label, self.rest_label):
            label.setStyleSheet("font-size: 12px;")
            totals_layout.addWidget(label)
        totals_layout.addStretch()
        layout.addLayout(totals_layout)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

    def set_summaries(self, daily: list[DailySummary], week: WeekSummary | None = None) -> None:
        self.table.setRowCount(len(daily))
        for row, summary in enumerate(daily):
            values = [
                summary.day.strftime("%d.%m.%Y"),
                format_minutes(summary.drive_min),
                format_minutes(summary.work_min),
                format_minutes(summary.availability_min),
                format_minutes(summary.rest_min),
                str(summary.odometer_km) if summary.odometer_km is not None else "—",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, summary.day)
                if col > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

        if week:
            self.drive_label.setText(f"{_tr('Jazda: ')}{format_minutes(week.drive_min)}")
            self.work_label.setText(f"{_tr('Praca: ')}{format_minutes(week.work_min)}")
            self.avail_label.setText(f"{_tr('Dyspozycyjnosc: ')}{format_minutes(week.availability_min)}")
            self.rest_label.setText(f"{_tr('Odpoczynek: ')}{format_minutes(week.rest_min)}")
        else:
            self.drive_label.setText(_tr("Jazda: —"))
            self.work_label.setText(_tr("Praca: —"))
            self.avail_label.setText(_tr("Dyspozycyjnosc: —"))
            self.rest_label.setText(_tr("Odpoczynek: —"))

    def selected_day(self) -> date | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        stored = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(stored, date):
            return stored
        return None
