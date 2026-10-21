from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ddd_analyzer.models import NightWorkWarning
from ddd_analyzer.night_work import NightWorkAnalysis
from ddd_analyzer.summarize import format_minutes


class NightWorkPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel("—")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [_tr("Od pracy"), _tr("Do pracy"), _tr("Czas pracy"), _tr("W porze nocnej"), _tr("Nadwyzka")]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(90)
        layout.addWidget(self.table)

        hint = QLabel(
            _tr("To nie jest limit jazdy 561/2006 (56 h/tydz.), lecz limit czasu pracy (jazda + inna praca) "
            "w okresie miedzy odpoczynkami >=9 h, gdy choć 1 minuta przypada w porze nocnej "
            "(domyslnie 00:00–04:00 w wybranej strefie: Czas PL / UTC karty).")
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #546e7a; font-size: 11px;")
        layout.addWidget(hint)

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.status_label.setText("—")
        self.status_label.setStyleSheet("")

    def set_analysis(self, analysis: NightWorkAnalysis | None) -> None:
        if analysis is None:
            self.clear()
            return

        window = analysis.night_window.label
        if not analysis.periods:
            self.table.setRowCount(0)
            self.status_label.setText(f"{_tr('Brak okresow pracy w danych (pora nocna ')}{window}).")
            self.status_label.setStyleSheet("color: #546e7a;")
            return

        if analysis.has_violations:
            self.status_label.setText(
                f"{_tr('Wykryto ')}{len(analysis.violations)} naruszenie/naruszen (limit 10 h pracy w porze nocnej {window}). Lacznie {analysis.night_limited_count} okresow z praca nocna."
            )
            self.status_label.setStyleSheet("color: #c62828; font-weight: bold;")
        else:
            self.status_label.setText(
                f"OK — {analysis.night_limited_count} okres(ow) z praca w porze nocnej {window}, "
                f"bez przekroczenia limitu 10 h miedzy odpoczynkami."
            )
            self.status_label.setStyleSheet("color: #2e7d32; font-weight: bold;")

        rows = analysis.violations
        self.table.setRowCount(len(rows))
        for row, period in enumerate(rows):
            values = [
                period.start.strftime("%d.%m.%Y %H:%M"),
                period.end.strftime("%d.%m.%Y %H:%M"),
                format_minutes(period.work_min),
                format_minutes(period.night_work_min),
                format_minutes(period.over_min),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setForeground(Qt.GlobalColor.red)
                font = cell.font()
                font.setBold(True)
                cell.setFont(font)
                self.table.setItem(row, col, cell)

    @staticmethod
    def warnings_from_analysis(analysis: NightWorkAnalysis) -> list[NightWorkWarning]:
        window = analysis.night_window.label
        return [
            NightWorkWarning(
                period_start=p.start,
                period_end=p.end,
                work_min=p.work_min,
                night_work_min=p.night_work_min,
                over_min=p.over_min,
                night_window_label=window,
            )
            for p in analysis.violations
        ]
