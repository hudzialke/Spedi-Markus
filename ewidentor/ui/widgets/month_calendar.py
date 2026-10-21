from __future__ import annotations

from spedi.i18n import tr as _tr
from calendar import monthcalendar
from datetime import date
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from ewidentor.ui import theme as T
_WEEKDAYS = (_tr("Pn"), _tr("Wt"), _tr("Śr"), _tr("Cz"), _tr("Pt"), _tr("So"), _tr("Nd"))
class _DayCell(QLabel):
    clicked = Signal(object)  # date | None
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._day: date | None = None
        self._selected = False
        self._heat = 0.0
        self._absence = False
        self._sunday_work = False
        self._manual = False
        self._empty = True
        self._refresh_style()
    def set_day_data(
        self,
        day: date | None,
        *,
        heat: float = 0.0,
        absence: bool = False,
        sunday_work: bool = False,
        manual: bool = False,
        empty: bool = True,
    ) -> None:
        self._day = day
        self._heat = max(0.0, min(1.0, heat))
        self._absence = absence
        self._sunday_work = sunday_work
        self._manual = manual
        self._empty = empty
        if day is None:
            self.setText("")
            self.setEnabled(False)
        else:
            self.setEnabled(True)
            self.setText(str(day.day))
        self._refresh_style()
    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._refresh_style()
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._day is not None and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._day)
        super().mousePressEvent(event)
    def _refresh_style(self) -> None:
        if self._day is None:
            self.setStyleSheet(
                f"background: transparent; border: none; color: {T.TEXT_MUTED};"
            )
            return
        left = "transparent"
        if self._absence:
            bg = T.ABSENCE_BG
            left = T.WARN
        elif self._sunday_work:
            bg = T.WN_BG
            left = T.DANGER
        elif self._manual:
            bg = T.MANUAL_BG
            left = T.INFO_MANUAL
        elif self._empty:
            bg = T.BG_ELEVATED
        else:
            bg = T.heat_color(self._heat)
            left = T.ACCENT
        border = T.ACCENT if self._selected else T.BORDER
        weight = "700" if self._selected else "500"
        text = (
            T.TEXT_ON_ACCENT
            if (
                not self._empty
                and self._heat > 0.55
                and not self._absence
                and not self._sunday_work
                and not self._manual
            )
            else T.TEXT_PRIMARY
        )
        self.setStyleSheet(
            f"background: {bg}; border: 2px solid {border}; border-radius: {T.RADIUS_SM}px; "
            f"border-left: 3px solid {left}; "
            f"color: {text}; font-weight: {weight}; padding: 2px;"
        )
class MonthCalendar(QWidget):
    """Siatka miesiąca z forest heat-mapą czasu pracy."""
    day_selected = Signal(object)  # date
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._year = date.today().year
        self._month = date.today().month
        self._selected: date | None = None
        self._cells: list[_DayCell] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        head = QGridLayout()
        for i, name in enumerate(_WEEKDAYS):
            lab = QLabel(name)
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet(f"color: {T.TEXT_SECONDARY}; font-weight: 600;")
            head.addWidget(lab, 0, i)
        root.addLayout(head)
        self._grid = QGridLayout()
        self._grid.setSpacing(4)
        for r in range(6):
            for c in range(7):
                cell = _DayCell()
                cell.clicked.connect(self._on_cell)
                self._cells.append(cell)
                self._grid.addWidget(cell, r, c)
        root.addLayout(self._grid, 1)
        hint = QLabel(
            _tr("Kolory: żółty = nieobecność · czerwony = praca nd/św · "
            "niebieski = ręczny · zieleń = natężenie pracy")
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
    def _on_cell(self, day: date) -> None:
        self.set_selected_day(day)
        self.day_selected.emit(day)
    def set_selected_day(self, day: date | None) -> None:
        self._selected = day
        for cell in self._cells:
            cell.set_selected(cell._day == day if day is not None else False)
    def set_month(
        self,
        year: int,
        month: int,
        day_stats: dict[date, dict],
    ) -> None:
        """
        day_stats[day] = {
            work_min, absence: bool, sunday_work: bool, manual: bool
        }
        """
        self._year = year
        self._month = month
        weeks = monthcalendar(year, month)
        max_work = max((int(s.get("work_min", 0)) for s in day_stats.values()), default=0)
        idx = 0
        for week in weeks:
            for day_num in week:
                cell = self._cells[idx]
                idx += 1
                if day_num == 0:
                    cell.set_day_data(None)
                    continue
                d = date(year, month, day_num)
                st = day_stats.get(d, {})
                work = int(st.get("work_min", 0))
                heat = (work / max_work) if max_work > 0 else 0.0
                cell.set_day_data(
                    d,
                    heat=heat,
                    absence=bool(st.get("absence")),
                    sunday_work=bool(st.get("sunday_work")),
                    manual=bool(st.get("manual")),
                    empty=work <= 0 and not st.get("absence"),
                )
        while idx < len(self._cells):
            self._cells[idx].set_day_data(None)
            idx += 1
        self.set_selected_day(
            self._selected
            if self._selected
            and self._selected.month == month
            and self._selected.year == year
            else None
        )
