from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from ddd_analyzer.display_tz import DEFAULT_ZONE, DisplayZone, format_dt
from ddd_analyzer.models import ClassifiedRest, RestAnalysisSummary, RestKind
from ddd_analyzer.reg561 import significant_rests
from ddd_analyzer.summarize import format_minutes


class RestPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zone: DisplayZone = DEFAULT_ZONE
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.title = QLabel(_tr("Odpoczynki 561/2006 (ciagle bloki)"))
        self.title.setStyleSheet("font-weight: bold;")
        self.summary_label = QLabel("—")
        header.addWidget(self.title)
        header.addStretch()
        header.addWidget(self.summary_label)
        layout.addLayout(header)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                _tr("Od"),
                _tr("Do"),
                _tr("Czas"),
                _tr("Typ"),
                _tr("Kompensacja"),
                _tr("Dlug utworzony"),
                _tr("Dlug po"),
                "Status",
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(60)
        layout.addWidget(self.table)

        hint = QLabel(
            "Pokazane sa bloki >= 9h. Tygodniowy skrocony (24–45h) tworzy dlug kompensacji "
            "(45h − dlugosc). Pojedyncza rekompensata jest odbierana jednorazowo. Po dwoch "
            "kolejnych skroconych OT obie rekompensaty musza poprzedzac regularny OT 45h."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #546e7a; font-size: 11px;")
        layout.addWidget(hint)
        self.ferry_note = QLabel()
        self.ferry_note.setWordWrap(True)
        self.ferry_note.setVisible(False)
        layout.addWidget(self.ferry_note)

    def set_conditions(self, conditions):
        from ddd_analyzer.specific_conditions import ferry_intervals, FERRY_REVIEW
        intervals = ferry_intervals(conditions)
        self.ferry_note.setVisible(bool(intervals))
        self.ferry_note.setText(FERRY_REVIEW if intervals else '')

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.summary_label.setText("—")
        self.summary_label.setStyleSheet("")

    def set_summary(
        self,
        summary: RestAnalysisSummary | None,
        *,
        zone: DisplayZone = DEFAULT_ZONE,
    ) -> None:
        self._zone = zone
        if summary is None:
            self.clear()
            return

        rows = significant_rests(summary.classified)
        self.table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [
                format_dt(item.start, zone),
                format_dt(item.end, zone),
                format_minutes(item.duration_min),
                item.kind_label_pl,
                format_minutes(item.compensation_min),
                format_minutes(item.debt_created_min),
                format_minutes(item.debt_after_min),
                item.status,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if item.kind in {RestKind.WEEKLY_REDUCED, RestKind.WEEKLY_REGULAR}:
                    font = cell.font()
                    font.setBold(True)
                    cell.setFont(font)
                if item.status == "zalegla":
                    cell.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, col, cell)

        unpaid = summary.unpaid_compensation_min
        unpaid_txt = format_minutes(unpaid)
        self.summary_label.setText(
            f"{_tr('Tyg. skrocone: ')}{summary.weekly_reduced_count}  ·  Tyg. regularne: {summary.weekly_regular_count}  ·  Niesplacona kompensacja: {unpaid_txt}"
        )
        if unpaid > 0:
            self.summary_label.setStyleSheet("color: #c62828; font-weight: bold;")
        else:
            self.summary_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
