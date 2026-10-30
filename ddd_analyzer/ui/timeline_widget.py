from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date, datetime, timedelta

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
from ddd_analyzer.display_tz import format_dt, utc_naive_to_zone
from ddd_analyzer.summarize import format_minutes, segment_time_label, segments_for_day
from ddd_analyzer.specific_conditions import ferry_details, ferry_intervals, segment_bounds, FERRY_COLOR


class TimelineWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: list[ActivitySegment] = []
        self._day: date | None = None
        self._zone: DisplayZone = DEFAULT_ZONE
        self._open_activity = None
        self._specific_conditions = ()
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
        ferry_legend = QLabel(_tr('■ Prom/kolej (warunek DDD)'))
        ferry_legend.setStyleSheet(f'color: {FERRY_COLOR}; font-size: 11px;')
        legend.addWidget(ferry_legend)
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
        self.conditions_table = QTableWidget(0, 2)
        self.conditions_table.setHorizontalHeaderLabels([_tr('Czas'), _tr('Warunki szczególne DDD')])
        self.conditions_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.conditions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.conditions_table.verticalHeader().setVisible(False)
        self.conditions_table.setMaximumHeight(110)
        layout.addWidget(self.conditions_table)

    def set_day(
        self,
        day: date,
        segments: list[ActivitySegment],
        *,
        zone: DisplayZone = DEFAULT_ZONE,
        open_activity=None,
        specific_conditions=(),
    ) -> None:
        self._day = day
        self._specific_conditions = specific_conditions
        self._zone = zone
        self._segments = segments_for_day(segments, day)
        self._open_activity = open_activity if open_activity and open_activity.start.date() == day else None
        zone_note = "UTC" if zone == "UTC" else "PL"
        self.title.setText(f"{_tr('Oś czasu — ')}{day.strftime('%d.%m.%Y')} ({zone_note})")
        self.canvas.set_segments(self._segments)
        self.canvas.set_conditions(day, specific_conditions, zone)
        self.canvas.update()
        self._fill_blocks_table()
        events = [c for c in specific_conditions if c.occurred_at.date() == day]
        self.conditions_table.setRowCount(len(events))
        self.conditions_table.setVisible(bool(events))
        for row, condition in enumerate(events):
            self.conditions_table.setItem(row, 0, QTableWidgetItem(format_dt(condition.occurred_at, zone, '%d.%m.%Y %H:%M:%S')))
            self.conditions_table.setItem(row, 1, QTableWidgetItem(_tr(condition.label)))

    def _fill_blocks_table(self) -> None:
        self.blocks_table.setRowCount(len(self._segments) + int(self._open_activity is not None))
        for row, segment in enumerate(self._segments):
            start, end, duration = segment_time_label(segment, self._zone)
            label = ACTIVITY_LABELS_PL[segment.activity]
            detail = ferry_details(self._specific_conditions, *segment_bounds(segment), self._zone)
            if detail:
                label += ' · ' + _tr('Prom/kolej')
            color = ACTIVITY_COLORS[segment.activity]

            values = [start, end, label, duration]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if detail:
                    item.setToolTip(detail)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 2:
                    item.setForeground(QColor(color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.blocks_table.setItem(row, col, item)
        if self._open_activity:
            row = len(self._segments)
            values = [format_dt(self._open_activity.start, self._zone, '%H:%M'),
                      _tr('Nieznany koniec'), ACTIVITY_LABELS_PL[self._open_activity.activity], _tr('Nieustalony')]
            for col, value in enumerate(values):
                self.blocks_table.setItem(row, col, QTableWidgetItem(value))


class _TimelineCanvas(QWidget):
    HOUR_MARKS = (0, 6, 12, 18, 24)

    def __init__(self) -> None:
        super().__init__()
        self._segments: list[ActivitySegment] = []
        self._ferry_ranges = ()
        self._day = None
        self._zone = DEFAULT_ZONE
        self.setMinimumHeight(92)

    def set_segments(self, segments: list[ActivitySegment]) -> None:
        self._segments = segments

    def set_conditions(self, day, conditions, zone=DEFAULT_ZONE):
        self._day = day
        self._zone = zone
        self._ferry_ranges = ferry_intervals(conditions)

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

        if self._day:
            midnight = datetime.combine(self._day, datetime.min.time())
            tomorrow = midnight + timedelta(days=1)
            for interval in self._ferry_ranges:
                if not interval.overlaps(midnight, tomorrow):
                    continue
                lo = (max(midnight, interval.start) - midnight).total_seconds() / 86400
                hi = (min(tomorrow, interval.end) - midnight).total_seconds() / 86400 if interval.end else lo
                x = margin_x + int(width * lo)
                painter.fillRect(x, top + 1, max(3, int(width * (hi-lo))), 5, QColor(FERRY_COLOR))
        painter.setPen(QColor("#546e7a"))
        for hour in self.HOUR_MARKS:
            x = margin_x + int(width * hour / 24)
            painter.drawLine(x, top + bar_height + 2, x, top + bar_height + 8)
            instant = datetime.combine(self._day, datetime.min.time()) + timedelta(hours=hour) if self._day else None
            label = utc_naive_to_zone(instant, self._zone).strftime('%H:%M') if instant else f'{hour:02d}:00'
            painter.drawText(x - 12, top + bar_height + 22, label)

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
