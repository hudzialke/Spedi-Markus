from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ddd_analyzer.models import ACTIVITY_COLORS, ACTIVITY_LABELS_PL, ActivitySegment, ActivityType
from ddd_analyzer.display_tz import DEFAULT_ZONE, DisplayZone
from ddd_analyzer.summarize import format_minutes, segment_time_label, segments_for_day


class TimelineWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[ActivitySegment] = []
        self._day: date | None = None
        self._zone: DisplayZone = DEFAULT_ZONE
        self.setMinimumHeight(120)

        layout = QVBoxLayout(self)
        self.title = QLabel(_tr("Oś czasu — wybierz dzień"))
        self.title.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.title)

        self.canvas = _TimelineCanvas()
        layout.addWidget(self.canvas)

        legend = QHBoxLayout()
        for activity in ActivityType:
            label = QLabel(f"■ {ACTIVITY_LABELS_PL[activity]}")
            label.setStyleSheet(f"color: {ACTIVITY_COLORS[activity]}; font-size: 11px;")
            legend.addWidget(label)
        legend.addStretch()
        layout.addLayout(legend)

        blocks_label = QLabel(_tr("Bloki aktywności — dokładne godziny"))
        blocks_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        layout.addWidget(blocks_label)

        self.blocks_table = QTableWidget(0, 4)
        self.blocks_table.setHorizontalHeaderLabels([_tr("Od"), _tr("Do"), _tr("Aktywność"), _tr("Czas trwania")])
        self.blocks_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.blocks_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.blocks_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.blocks_table.setAlternatingRowColors(True)
        self.blocks_table.verticalHeader().setVisible(False)
        self.blocks_table.setMinimumHeight(140)
        layout.addWidget(self.blocks_table)

    def set_day(
        self,
        day: date,
        segments: list[ActivitySegment],
        *,
        zone: DisplayZone = DEFAULT_ZONE,
    ) -> None:
        self._day = day
        self._zone = zone
        self._segments = segments_for_day(segments, day)
        zone_note = "UTC" if zone == "UTC" else "PL"
        self.title.setText(f"{_tr('Oś czasu — ')}{day.strftime('%d.%m.%Y')} ({zone_note})")
        self.canvas.set_segments(self._segments)
        self.canvas.update()
        self._fill_blocks_table()

    def _fill_blocks_table(self) -> None:
        self.blocks_table.setRowCount(len(self._segments))
        for row, segment in enumerate(self._segments):
            start, end, duration = segment_time_label(segment, self._zone)
            label = ACTIVITY_LABELS_PL[segment.activity]
            color = ACTIVITY_COLORS[segment.activity]

            values = [start, end, label, duration]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 2:
                    item.setForeground(QColor(color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.blocks_table.setItem(row, col, item)


class _TimelineCanvas(QWidget):
    HOUR_MARKS = (0, 6, 12, 18, 24)

    def __init__(self) -> None:
        super().__init__()
        self._segments: list[ActivitySegment] = []
        self.setMinimumHeight(64)

    def set_segments(self, segments: list[ActivitySegment]) -> None:
        self._segments = segments

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin_x = 8
        top = 8
        bar_height = 28
        width = self.width() - 2 * margin_x
        if width <= 0:
            return

        painter.fillRect(margin_x, top, width, bar_height, QColor("#eceff1"))
        painter.setPen(QPen(QColor("#b0bec5"), 1))
        painter.drawRect(margin_x, top, width, bar_height)

        for segment in self._segments:
            x1 = margin_x + int(width * segment.start_minute / 1440)
            x2 = margin_x + int(width * segment.end_minute / 1440)
            if x2 <= x1:
                continue
            color = QColor(ACTIVITY_COLORS[segment.activity])
            painter.fillRect(x1, top + 1, x2 - x1, bar_height - 2, color)

        painter.setPen(QColor("#546e7a"))
        for hour in self.HOUR_MARKS:
            x = margin_x + int(width * hour / 24)
            painter.drawLine(x, top + bar_height + 2, x, top + bar_height + 8)
            painter.drawText(x - 12, top + bar_height + 22, f"{hour:02d}:00")

        if self._segments:
            totals: dict[ActivityType, int] = {}
            for segment in self._segments:
                totals[segment.activity] = totals.get(segment.activity, 0) + segment.duration_min
            parts = [
                f"{ACTIVITY_LABELS_PL[activity]}: {format_minutes(mins)}"
                for activity, mins in totals.items()
                if mins > 0
            ]
            painter.drawText(margin_x, top + bar_height + 40, "  ·  ".join(parts))
