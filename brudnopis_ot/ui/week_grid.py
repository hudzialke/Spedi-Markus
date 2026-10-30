from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date, datetime, timedelta

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QRubberBand, QWidget

from brudnopis_ot.core.calendar_weeks import DAY_LABELS_PL, format_duration_hm, format_week_label
from brudnopis_ot.core.display_tz import (
    DEFAULT_ZONE,
    DisplayZone,
    format_dt,
    local_time_candidates,
    utc_naive_to_zone,
)
from brudnopis_ot.core.models import (
    ACTIVITY_COLORS,
    COMPENSATION_BAR_COLOR,
    ActivityKind,
    Scenario,
    ValidationResult,
)


class WeekGridWidget(QWidget):
    """Fortras-like multi-week activity timeline."""

    block_clicked = Signal(str)
    delete_block_requested = Signal(str)
    set_week1_anchor = Signal(str)  # block_id → start tygodnia 1
    empty_clicked = Signal(object)  # datetime (UTC, canonical)
    zoom_changed = Signal(float)
    lens_mode_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scenario: Scenario | None = None
        self._result: ValidationResult | None = None
        self._selected_ids: set[str] = set()
        self._highlight_day: date | None = None
        self._zone: DisplayZone = DEFAULT_ZONE
        self._hours_per_day_px = 28.0
        self._label_width = 150.0
        self._row_height = 78.0
        self._header_height = 28.0
        self._hit_blocks: list[tuple[str, QRectF]] = []
        self._week_hits = {}
        self._blocks_by_id = {}
        self._display_times = {}
        self._layout_dirty = True
        self.editable = True
        self._data_issues = []
        self._lens_mode = False
        self._lens_origin = None
        self._lens_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(360)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_lens_mode(self, enabled: bool) -> None:
        self._lens_mode = enabled
        self._lens_origin = None
        self._lens_band.hide()
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.PointingHandCursor)
        self.lens_mode_changed.emit(enabled)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._lens_mode:
            self.set_lens_mode(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._lens_mode and self._lens_origin is not None:
            self._lens_band.setGeometry(QRect(self._lens_origin, event.position().toPoint()).normalized())
            event.accept()
            return
        from brudnopis_ot.ui.data_issues import issue_text
        warning = ''
        for identity, rect in reversed(self._hit_blocks):
            if rect.contains(event.position()):
                block = self._blocks_by_id.get(identity)
                warning = issue_text(block, self._data_issues) if block else ''
                if block and self._scenario:
                    from ddd_analyzer.specific_conditions import ferry_details
                    detail = ferry_details(self._scenario.specific_conditions, block.start, block.end, self._zone)
                    warning = '\n'.join(filter(None, (warning, detail)))
                break
        self.setToolTip(warning)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._lens_mode and self._lens_origin is not None and event.button() == Qt.MouseButton.LeftButton:
            from brudnopis_ot.ui.timeline_lens import selected_ranges, TimelineLensDialog
            rectangle = QRectF(QRect(self._lens_origin, event.position().toPoint()).normalized())
            ranges = selected_ranges(self, rectangle)
            self.set_lens_mode(False)
            if ranges:
                dialog = TimelineLensDialog(self, ranges)
                try:
                    if dialog.exec() and dialog.selected_id:
                        if dialog.selected_action == 'anchor':
                            self.set_week1_anchor.emit(dialog.selected_id)
                        elif dialog.selected_action == 'edit':
                            self.block_clicked.emit(dialog.selected_id)
                        else:
                            self.set_selected_ids({dialog.selected_id})
                finally:
                    dialog.deleteLater()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_display_zone(self, zone: DisplayZone) -> None:
        if self._zone == zone:
            return
        self._zone = zone
        self._invalidate_layout()
        self.update()

    def _disp(self, instant: datetime) -> datetime:
        key = (instant, instant.fold)
        if key not in self._display_times:
            self._display_times[key] = utc_naive_to_zone(instant, self._zone)
        return self._display_times[key]

    def _invalidate_layout(self) -> None:
        self._layout_dirty = True
        self._display_times.clear()
        self._week_hits.clear()
        self._hit_blocks.clear()

    def _group_by_week(self, items, bounds):
        groups = [[] for _ in range(self._scenario.weeks)]
        for item in items:
            start, end = bounds(item)
            first = max(0, self._week_of(self._disp(start)))
            last = min(len(groups) - 1, self._week_of(self._disp(end)))
            for week in range(first, last + 1):
                groups[week].append(item)
        return groups

    def _prepare_layout(self):
        self._blocks_by_id = {block.id: block for block in self._scenario.blocks}
        self._week_blocks = self._group_by_week(self._scenario.sorted_blocks(), lambda b: (b.start, b.end))
        result = self._result
        self._week_rests = self._group_by_week(result.weekly_rests if result else (), lambda r: (r.start, r.end))
        self._week_findings = self._group_by_week(
            [f for f in result.findings if f.severity == 'violation' and f.underline_range] if result else (),
            lambda f: (f.underline_range.start, f.underline_range.end))
        self._week_six = self._group_by_week(result.six_day_windows if result else (), lambda w: (w.start, w.end))
        self._week_comp = self._group_by_week(result.compensation_windows if result else (), lambda w: (w.start, w.start))
        self._layout_dirty = False

    def set_scenario(self, scenario: Scenario | None) -> None:
        self.set_lens_mode(False)
        self._scenario = scenario
        self._invalidate_layout()
        self._update_size()
        self.update()

    def set_validation(self, result: ValidationResult | None) -> None:
        self._result = result
        self._invalidate_layout()
        self.update()

    def set_data_issues(self, issues) -> None:
        self._data_issues = list(issues)
        self.update()

    def set_selected_ids(self, ids: set[str] | list[str] | tuple[str, ...]) -> None:
        self._selected_ids = set(ids)
        self.update()

    def set_highlight_day(self, day: date | None) -> None:
        self._highlight_day = day
        self.update()

    def zoom_in(self) -> None:
        self._hours_per_day_px = min(64.0, self._hours_per_day_px + 4.0)
        self._update_size()
        self.zoom_changed.emit(self._hours_per_day_px)
        self.update()

    def zoom_out(self) -> None:
        self._hours_per_day_px = max(14.0, self._hours_per_day_px - 4.0)
        self._update_size()
        self.zoom_changed.emit(self._hours_per_day_px)
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def _px_per_hour_effective(self) -> float:
        # Map zoom knob: 14→3 px/h, 28→5, 64→10
        return 2.0 + (self._hours_per_day_px - 14.0) * (8.0 / 50.0)

    def _update_size(self) -> None:
        self._week_hits.clear()
        self._hit_blocks.clear()
        if self._scenario is None:
            self.setMinimumWidth(800)
            return
        px_h = self._px_per_hour_effective()
        content_w = 7 * 24 * px_h
        total_w = int(self._label_width + content_w + 24)
        total_h = int(self._header_height + self._scenario.weeks * self._row_height + 16)
        self.setMinimumWidth(total_w)
        self.setMinimumHeight(total_h)

    def _x_for(self, instant: datetime) -> float:
        assert self._scenario is not None
        week_i = (instant.date() - self._scenario.week_start).days // 7
        week_monday = self._scenario.week_start + timedelta(days=7 * week_i)
        week_start_dt = datetime.combine(week_monday, datetime.min.time())
        hours = (instant - week_start_dt).total_seconds() / 3600.0
        return self._label_width + hours * self._px_per_hour_effective()

    def _week_of(self, instant: datetime) -> int:
        assert self._scenario is not None
        return (instant.date() - self._scenario.week_start).days // 7

    def _datetime_at(self, pos: QPointF) -> datetime | None:
        if self._scenario is None:
            return None
        if pos.x() < self._label_width:
            return None
        week_i = int((pos.y() - self._header_height) // self._row_height)
        if week_i < 0 or week_i >= self._scenario.weeks:
            return None
        px_h = self._px_per_hour_effective()
        hours = (pos.x() - self._label_width) / px_h
        hours = max(0.0, min(7 * 24 - 0.01, hours))
        monday = self._scenario.week_start + timedelta(days=7 * week_i)
        # Snap to 15 minutes
        total_minutes = int(hours * 60)
        total_minutes = (total_minutes // 15) * 15
        return datetime.combine(monday, datetime.min.time()) + timedelta(minutes=total_minutes)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._lens_mode and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._lens_origin = pos.toPoint()
            self._lens_band.setGeometry(QRect(self._lens_origin, self._lens_origin))
            self._lens_band.show()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton and self._scenario is not None:
            for block_id, rect in self._hit_blocks:
                if rect.contains(pos):
                    menu = QMenu(self)
                    edit = menu.addAction(_tr("Edytuj blok…"))
                    delete = menu.addAction(_tr("Usuń blok"))
                    menu.addSeparator()
                    act = menu.addAction(_tr("Ustaw jako start tygodnia 1"))
                    for action in (edit, delete, act):
                        action.setEnabled(self.editable)
                    chosen = menu.exec(event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos())
                    if chosen is act:
                        self.set_week1_anchor.emit(block_id)
                    elif chosen is edit:
                        self.block_clicked.emit(block_id)
                    elif chosen is delete:
                        self.delete_block_requested.emit(block_id)
                    return
            return super().mousePressEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or self._scenario is None:
            return super().mousePressEvent(event)
        for block_id, rect in self._hit_blocks:
            if rect.contains(pos):
                self.block_clicked.emit(block_id)
                return
        instant = self._datetime_at(pos)
        if instant is not None and self.editable:
            utc = self._clicked_local_to_utc(instant)
            if utc is not None:
                self.empty_clicked.emit(utc)
        super().mousePressEvent(event)

    def _clicked_local_to_utc(self, instant: datetime) -> datetime | None:
        candidates = local_time_candidates(instant, self._zone)
        if not candidates:
            QMessageBox.warning(
                self,
                _tr("Nieistniejąca godzina"),
                f"{_tr('Godzina ')}{instant:%d.%m.%Y %H:%M}{_tr(' nie istnieje w czasie polskim z powodu przejścia na czas letni.')}",
            )
            return None
        if len(candidates) == 1:
            return candidates[0].utc
        descriptions = [
            f"{'pierwsze' if index == 0 else 'drugie'} {instant:%H:%M} "
            f"({item.offset_label}) → {item.utc:%H:%M} UTC"
            for index, item in enumerate(candidates)
        ]
        selected, ok = QInputDialog.getItem(
            self,
            _tr("Godzina występuje dwa razy"),
            _tr("Wybierz właściwe wystąpienie godziny na osi:"),
            descriptions,
            0,
            False,
        )
        return candidates[descriptions.index(selected)].utc if ok else None

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.fillRect(self.rect(), QColor("#e9eef2"))
        if self._scenario is None:
            painter.setPen(QColor("#5a6672"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, _tr("Brak scenariusza"))
            return

        if self._layout_dirty:
            self._prepare_layout()

        px_h = self._px_per_hour_effective()
        content_w = 7 * 24 * px_h

        # Day headers
        header_font = QFont()
        header_font.setPointSize(9)
        painter.setFont(header_font)
        for day_i, label in enumerate(DAY_LABELS_PL):
            x = self._label_width + day_i * 24 * px_h
            painter.setPen(QColor("#374151"))
            painter.drawText(QRectF(x, 4, 24 * px_h, 20), Qt.AlignmentFlag.AlignCenter, label)

        for week_i in range(self._scenario.weeks):
            top = self._header_height + week_i * self._row_height
            # Labels and deadline markers extend outside their activity row.
            # Keep hit rectangles for rows unaffected by a partial repaint.
            if top + self._row_height + 24 < event.rect().top() or top - 24 > event.rect().bottom():
                continue
            week_hits = []
            self._week_hits[week_i] = week_hits
            monday = self._scenario.week_start + timedelta(days=7 * week_i)
            # Label
            painter.setPen(QColor("#111827"))
            lab_font = QFont()
            lab_font.setPointSize(8)
            painter.setFont(lab_font)
            painter.drawText(
                QRectF(4, top + 8, self._label_width - 8, self._row_height - 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                format_week_label(monday),
            )

            bar_top = top + 18
            bar_h = 28.0
            # Marker kotwicy tygodnia 1
            if week_i == 0:
                painter.setPen(QPen(QColor("#2563EB"), 2))
                painter.drawLine(
                    QPointF(self._label_width, bar_top - 2),
                    QPointF(self._label_width, bar_top + bar_h + 2),
                )
                mark_font = QFont()
                mark_font.setPointSize(7)
                mark_font.setBold(True)
                painter.setFont(mark_font)
                painter.setPen(QColor("#2563EB"))
                painter.drawText(
                    QRectF(self._label_width + 2, top + 2, 80, 14),
                    Qt.AlignmentFlag.AlignLeft,
                    _tr("◀ tydz. 1"),
                )
            # Day background strips
            for day_i in range(7):
                x = self._label_width + day_i * 24 * px_h
                day_date = monday + timedelta(days=day_i)
                if self._highlight_day is not None and day_date == self._highlight_day:
                    bg = QColor("#FEF08C")
                elif day_i < 5:
                    bg = QColor("#DBEAFE")
                else:
                    bg = QColor("#E0E7FF")
                painter.fillRect(QRectF(x, bar_top, 24 * px_h, bar_h), bg)
                painter.setPen(QPen(QColor("#93C5FD"), 1))
                painter.drawRect(QRectF(x, bar_top, 24 * px_h, bar_h))
                # Hour ticks 0/6/12/18
                tick_font = QFont()
                tick_font.setPointSize(7)
                painter.setFont(tick_font)
                for hour in (0, 6, 12, 18):
                    tx = x + hour * px_h
                    painter.setPen(QColor("#64748B"))
                    painter.drawLine(QPointF(tx, bar_top + bar_h), QPointF(tx, bar_top + bar_h + 4))
                    painter.drawText(
                        QRectF(tx - 8, bar_top + bar_h + 2, 16, 12),
                        Qt.AlignmentFlag.AlignCenter,
                        str(hour),
                    )

            # Blocks intersecting this week (positions in display zone)
            week_start = datetime.combine(monday, datetime.min.time())
            week_end = week_start + timedelta(days=7)
            for block in self._week_blocks[week_i]:
                bs = self._disp(block.start)
                be = self._disp(block.end)
                if be <= week_start or bs >= week_end:
                    continue
                seg_start = max(bs, week_start)
                seg_end = min(be, week_end)
                x0 = self._label_width + (seg_start - week_start).total_seconds() / 3600.0 * px_h
                x1 = self._label_width + (seg_end - week_start).total_seconds() / 3600.0 * px_h
                rect = QRectF(x0, bar_top + 2, max(2.0, x1 - x0), bar_h - 4)
                color = QColor(ACTIVITY_COLORS.get(block.kind, "#94A3B8"))
                if block.kind == ActivityKind.CARD_OUT:
                    painter.fillRect(rect, QColor("#F3F4F6"))
                    painter.setPen(QPen(QColor("#9CA3AF"), 1, Qt.PenStyle.DashLine))
                    painter.drawRect(rect)
                else:
                    painter.fillRect(rect, color)
                    if block.id in self._selected_ids:
                        painter.setPen(QPen(QColor("#0F172A"), 2))
                    else:
                        painter.setPen(QPen(QColor("#334155"), 1))
                    painter.drawRect(rect)

                # Duration label inside blocks longer than 9h (full block duration)
                if block.duration_hours > 9.0 and rect.width() >= 28:
                    painter.setPen(QColor("#0F172A"))
                    dur_font = QFont()
                    dur_font.setPointSize(7)
                    dur_font.setBold(True)
                    painter.setFont(dur_font)
                    painter.drawText(
                        rect,
                        Qt.AlignmentFlag.AlignCenter,
                        format_duration_hm(block.duration_hours),
                    )

                week_hits.append((block.id, rect))

                # Country flag at start
                if block.country_start and abs((seg_start - bs).total_seconds()) < 1:
                    painter.setPen(QColor("#0F172A"))
                    flag_font = QFont()
                    flag_font.setPointSize(7)
                    flag_font.setBold(True)
                    painter.setFont(flag_font)
                    painter.drawText(
                        QRectF(x0, bar_top - 14, 24, 12),
                        Qt.AlignmentFlag.AlignLeft,
                        block.country_start,
                    )

            # Condition overlay preserves the source activity underneath it.
            from ddd_analyzer.specific_conditions import ferry_intervals, FERRY_COLOR
            for interval in ferry_intervals(self._scenario.specific_conditions):
                begin = self._disp(interval.start)
                finish = self._disp(interval.end) if interval.end else begin
                if finish < week_start or begin >= week_end:
                    continue
                x0 = self._label_width + (max(begin, week_start)-week_start).total_seconds()/3600*px_h
                x1 = self._label_width + (min(finish, week_end)-week_start).total_seconds()/3600*px_h
                painter.fillRect(QRectF(x0, bar_top+2, max(4, x1-x0), 5), QColor(FERRY_COLOR))
                painter.setPen(QColor(FERRY_COLOR))
                mark_font = QFont()
                mark_font.setPointSize(7)
                painter.setFont(mark_font)
                painter.drawText(QRectF(x0, bar_top+bar_h+15, 110, 13), Qt.AlignmentFlag.AlignLeft, _tr('Prom/kolej'))

            # Weekly rest labels + compensation green strip (also on OD ≥9h)
            if self._result is not None:
                for wr in self._week_rests[week_i]:
                    wr_s, wr_e = self._disp(wr.start), self._disp(wr.end)
                    if wr_e <= week_start or wr_s >= week_end:
                        continue

                    # Green bar for compensation hours attached to this rest
                    if wr.compensation_hours > 0.05:
                        comp_start = (
                            wr_s
                            if wr.compensation_precedes_regular
                            else wr_e - timedelta(hours=wr.compensation_hours)
                        )
                        comp_end = (
                            wr_s + timedelta(hours=wr.compensation_hours)
                            if wr.compensation_precedes_regular
                            else wr_e
                        )
                        cs = max(comp_start, week_start, wr_s)
                        ce = min(comp_end, week_end, wr_e)
                        if ce > cs:
                            cx0 = (
                                self._label_width
                                + (cs - week_start).total_seconds() / 3600.0 * px_h
                            )
                            cx1 = (
                                self._label_width
                                + (ce - week_start).total_seconds() / 3600.0 * px_h
                            )
                            strip_h = 8.0
                            strip = QRectF(
                                cx0,
                                bar_top + bar_h - strip_h - 2,
                                max(2.0, cx1 - cx0),
                                strip_h,
                            )
                            painter.fillRect(strip, QColor(COMPENSATION_BAR_COLOR))
                            painter.setPen(QPen(QColor("#15803D"), 1))
                            painter.drawRect(strip)

                    if not wr.is_weekly:
                        continue

                    seg_start = max(wr_s, week_start)
                    mid = seg_start + (min(wr_e, week_end) - seg_start) / 2
                    mx = self._label_width + (mid - week_start).total_seconds() / 3600.0 * px_h
                    painter.setPen(QColor("#0C4A6E"))
                    lab = QFont()
                    lab.setPointSize(8)
                    lab.setBold(True)
                    painter.setFont(lab)
                    text = wr.label
                    if wr.compensation_hours > 0.5:
                        text += " +rek."
                    painter.drawText(
                        QRectF(mx - 40, bar_top - 2, 80, 14),
                        Qt.AlignmentFlag.AlignCenter,
                        text.split()[0] if text else "",
                    )
                    # duration under bar
                    painter.setPen(QColor("#334155"))
                    small = QFont()
                    small.setPointSize(7)
                    painter.setFont(small)
                    painter.drawText(
                        QRectF(mx - 30, bar_top + bar_h + 2, 60, 12),
                        Qt.AlignmentFlag.AlignCenter,
                        text.split()[-1] if " " in text else text,
                    )

                # Violation underlines
                for finding in self._week_findings[week_i]:
                    if finding.severity != "violation" or finding.underline_range is None:
                        continue
                    ur = finding.underline_range
                    ur_s, ur_e = self._disp(ur.start), self._disp(ur.end)
                    if ur_e <= week_start or ur_s >= week_end:
                        continue
                    s = max(ur_s, week_start)
                    e = min(ur_e, week_end)
                    ux0 = self._label_width + (s - week_start).total_seconds() / 3600.0 * px_h
                    ux1 = self._label_width + (e - week_start).total_seconds() / 3600.0 * px_h
                    painter.setPen(QPen(QColor("#DC2626"), 3))
                    y = bar_top + bar_h + 14
                    painter.drawLine(QPointF(ux0, y), QPointF(ux1, y))

                # Max 6x24h brackets (green) for six_day_windows intersecting week
                painter.setPen(QPen(QColor("#16A34A"), 2))
                for window in self._week_six[week_i]:
                    win_s, win_e = self._disp(window.start), self._disp(window.end)
                    if win_e <= week_start or win_s >= week_end:
                        continue
                    s = max(win_s, week_start)
                    e = min(win_e, week_end)
                    bx0 = self._label_width + (s - week_start).total_seconds() / 3600.0 * px_h
                    bx1 = self._label_width + (e - week_start).total_seconds() / 3600.0 * px_h
                    bracket_y = bar_top - 10
                    painter.drawLine(QPointF(bx0, bracket_y), QPointF(bx1, bracket_y))
                    painter.drawLine(QPointF(bx0, bracket_y), QPointF(bx0, bracket_y + 6))
                    painter.drawLine(QPointF(bx1, bracket_y), QPointF(bx1, bracket_y + 6))
                    if week_i == self._week_of(win_s) or (
                        win_s < week_start < win_e
                    ):
                        painter.setPen(QColor("#15803D"))
                        bf = QFont()
                        bf.setPointSize(7)
                        painter.setFont(bf)
                        painter.drawText(
                            QRectF(bx0, bracket_y - 12, max(60.0, bx1 - bx0), 12),
                            Qt.AlignmentFlag.AlignCenter,
                            _tr("Max. 6×24h"),
                        )
                        painter.setPen(QPen(QColor("#16A34A"), 2))
                    # Deadline tick: solid red vertical line (OT: 6×24h)
                    if week_start < win_e <= week_end:
                        dx = (
                            self._label_width
                            + (win_e - week_start).total_seconds() / 3600.0 * px_h
                        )
                        painter.setPen(QPen(QColor("#DC2626"), 2, Qt.PenStyle.SolidLine))
                        painter.drawLine(
                            QPointF(dx, bar_top - 14),
                            QPointF(dx, bar_top + bar_h + 4),
                        )
                        painter.setPen(QColor("#DC2626"))
                        df = QFont()
                        df.setPointSize(7)
                        df.setBold(True)
                        painter.setFont(df)
                        painter.drawText(
                            QRectF(dx - 28, bar_top - 26, 56, 12),
                            Qt.AlignmentFlag.AlignCenter,
                            format_dt(window.end, self._zone, "%d.%m %H:%M"),
                        )
                        painter.setPen(QPen(QColor("#16A34A"), 2))

                # Po parze 2× skrócony: rekompensaty muszą zakończyć się przed
                # granicą 6×24h. Pomarańczowa linia pokazuje ostatni moment,
                # w którym kierowca może zakończyć jazdę i zacząć cały blok.
                for window in self._week_comp[week_i]:
                    latest_start = self._disp(window.start)
                    regular_start = self._disp(window.end)
                    if not (week_start <= latest_start < week_end):
                        continue
                    sx = (
                        self._label_width
                        + (latest_start - week_start).total_seconds() / 3600.0 * px_h
                    )
                    painter.setPen(QPen(QColor("#EA580C"), 3, Qt.PenStyle.SolidLine))
                    painter.drawLine(
                        QPointF(sx, bar_top - 14),
                        QPointF(sx, bar_top + bar_h + 8),
                    )
                    painter.setPen(QColor("#C2410C"))
                    marker_font = QFont()
                    marker_font.setPointSize(7)
                    marker_font.setBold(True)
                    painter.setFont(marker_font)
                    painter.drawText(
                        QRectF(sx - 48, bar_top + bar_h + 10, 96, 14),
                        Qt.AlignmentFlag.AlignCenter,
                        f"{_tr('REK od ')}{format_dt(window.start, self._zone, '%d.%m %H:%M')}",
                    )

                    # Zakres od początku rekompensat do początku regularnych 45h.
                    if regular_start > latest_start:
                        segment_end = min(regular_start, week_end)
                        ex = (
                            self._label_width
                            + (segment_end - week_start).total_seconds() / 3600.0 * px_h
                        )
                        painter.setPen(QPen(QColor("#F97316"), 2, Qt.PenStyle.DashLine))
                        painter.drawLine(
                            QPointF(sx, bar_top + bar_h + 7),
                            QPointF(ex, bar_top + bar_h + 7),
                        )

        # Outer border of timeline
        self._hit_blocks = [hit for week in sorted(self._week_hits) for hit in self._week_hits[week]]
        from brudnopis_ot.ui.data_issues import issue_text, draw_issue_badge
        for identity, rect in self._hit_blocks if self._data_issues else ():
            block = self._blocks_by_id.get(identity)
            if block and issue_text(block, self._data_issues):
                draw_issue_badge(painter, rect)
        # An invalid record may occupy a gap rather than a known activity.
        for start, end, reason in self._data_issues:
            if any(b.start < end and b.end > start for b in self._scenario.blocks):
                continue
            moment = self._disp(start)
            row = (moment.date() - self._scenario.week_start).days // 7
            if 0 <= row < self._scenario.weeks:
                monday = datetime.combine(self._scenario.week_start + timedelta(days=row * 7), datetime.min.time())
                x = self._label_width + (moment - monday).total_seconds() / 3600 * px_h
                draw_issue_badge(painter, QRectF(x, self._header_height + row * self._row_height + 25, 13, 16))
        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.drawRect(
            QRectF(
                self._label_width,
                self._header_height,
                content_w,
                self._scenario.weeks * self._row_height,
            )
        )
