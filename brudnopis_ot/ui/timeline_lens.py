from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import datetime, timedelta
import weakref

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QScrollArea, QVBoxLayout, QWidget

from brudnopis_ot.core.models import ACTIVITY_COLORS, ACTIVITY_LABELS_PL, Block


def country_start_code(block: Block) -> str | None:
    return (block.country_start or '').strip().upper() or None


def country_start_description(block: Block) -> str:
    return f'kraj startu: {country_start_code(block) or "brak danych"}'


def selected_ranges(grid, rectangle: QRectF):
    if grid._scenario is None:
        return []
    bounds = QRectF(grid._label_width, grid._header_height,
                    168 * grid._px_per_hour_effective(), grid._scenario.weeks * grid._row_height)
    rect = rectangle.normalized().intersected(bounds)
    if rect.width() < 2 or rect.height() < 2:
        return []
    first = max(0, int((rect.top() - grid._header_height) // grid._row_height))
    last = min(grid._scenario.weeks - 1, int((rect.bottom() - grid._header_height - .001) // grid._row_height))
    hour_start = (rect.left() - grid._label_width) / grid._px_per_hour_effective()
    hour_end = (rect.right() - grid._label_width) / grid._px_per_hour_effective()
    return [(datetime.combine(grid._scenario.week_start + timedelta(days=week * 7), datetime.min.time()) + timedelta(hours=hour_start),
             datetime.combine(grid._scenario.week_start + timedelta(days=week * 7), datetime.min.time()) + timedelta(hours=hour_end))
            for week in range(first, last + 1)]


class DetailTimeline(QWidget):
    block_clicked = Signal(str)

    def __init__(self, grid, ranges, parent=None):
        super().__init__(parent)
        # The grid owns this dialog through Qt's parent hierarchy. A strong
        # reference back to that parent can destroy it reentrantly while the
        # deferred deletion of the detail widget is still in progress.
        self.grid, self.ranges = weakref.proxy(grid), ranges
        self.hits = []
        self.selected_id = None
        self.setMinimumSize(620, max(110, 110 * len(ranges)))
        self.setMouseTracking(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#f7f9fb'))
        self.hits.clear()
        width = self.width() - 36
        for index, (start, end) in enumerate(self.ranges):
            top = 110 * index
            seconds = (end - start).total_seconds()
            painter.setPen(QColor('#18212b'))
            painter.drawText(18, top + 20, f'{start:%d.%m.%Y %H:%M} — {end:%d.%m.%Y %H:%M}')
            painter.fillRect(QRectF(18, top + 32, width, 42), QColor('#dbeafe'))
            for tick in range(5):
                moment = start + (end - start) * tick / 4
                painter.drawText(QRectF(18 + tick * width / 4 - 35, top + 78, 70, 20), Qt.AlignmentFlag.AlignCenter, f'{moment:%H:%M}')
            for block in self.grid._scenario.sorted_blocks():
                bs, be = self.grid._disp(block.start), self.grid._disp(block.end)
                if be <= start or bs >= end:
                    continue
                x = 18 + (max(bs, start) - start).total_seconds() / seconds * width
                w = (min(be, end) - max(bs, start)).total_seconds() / seconds * width
                rect = QRectF(x, top + 32, max(2, w), 42)
                painter.fillRect(rect, QColor(ACTIVITY_COLORS[block.kind]))
                painter.setPen(QPen(QColor('#18212b'), 3 if block.id == self.selected_id else 1))
                painter.drawRect(rect)
                country = country_start_code(block)
                show_country = bool(country and rect.width() >= 34)
                if show_country:
                    painter.save()
                    badge = QRectF(rect.left() + 3, rect.top() + 11, 28, 20)
                    painter.fillRect(badge, QColor('#f7f9fb'))
                    painter.setPen(QColor('#18212b'))
                    font = painter.font()
                    font.setBold(True)
                    painter.setFont(font)
                    painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, country)
                    painter.restore()
                label = ACTIVITY_LABELS_PL[block.kind]
                label_rect = rect.adjusted(34 if show_country else 3, 0, -3, 0)
                if label_rect.width() >= painter.fontMetrics().horizontalAdvance(label) + 6:
                    painter.setPen(QColor('#ffffff') if QColor(ACTIVITY_COLORS[block.kind]).lightness() < 110 else QColor('#18212b'))
                    painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
                self.hits.append((block.id, rect, f'{label} · {bs:%d.%m %H:%M} — {be:%d.%m %H:%M} · {country_start_description(block)}'))
                from brudnopis_ot.ui.data_issues import issue_text, draw_issue_badge
                warning = issue_text(block, getattr(self.grid, '_data_issues', []))
                if warning:
                    draw_issue_badge(painter, rect)
                    self.hits[-1] = (block.id, rect, self.hits[-1][2] + '\n! ' + warning)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for identity, rect, _ in reversed(self.hits):
                if rect.contains(event.position()):
                    self.block_clicked.emit(identity)
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.setToolTip(next((label for _, rect, label in reversed(self.hits) if rect.contains(event.position())), ''))
        super().mouseMoveEvent(event)


class TimelineLensDialog(QDialog):
    def __init__(self, grid, ranges):
        super().__init__(grid)
        self.selected_id = None
        self.selected_action = 'edit'
        self.setWindowTitle(_tr('Lupa osi czasu — wybierz blok'))
        self.resize(940, 470)
        layout = QVBoxLayout(self)
        hint = QLabel(_tr('Zaznacz blok na powiększeniu lub liście, a następnie wybierz edycję albo start tygodnia 1. Daty są w strefie wyświetlania osi.'))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.detail = DetailTimeline(grid, ranges)
        self.detail.block_clicked.connect(self.select_block)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.detail)
        layout.addWidget(scroll, 2)
        self.blocks = QListWidget()
        for block in grid._scenario.sorted_blocks():
            bs, be = grid._disp(block.start), grid._disp(block.end)
            if any(bs < end and be > start for start, end in ranges):
                item = QListWidgetItem(f'{bs:%d.%m %H:%M} — {be:%d.%m %H:%M} · {ACTIVITY_LABELS_PL[block.kind]} · {country_start_description(block)}')
                item.setData(Qt.ItemDataRole.UserRole, block.id)
                from brudnopis_ot.ui.data_issues import issue_text
                warning = issue_text(block, getattr(grid, '_data_issues', []))
                if warning:
                    item.setText('! ' + item.text())
                    item.setToolTip(warning)
                self.blocks.addItem(item)
        self.blocks.itemActivated.connect(lambda item: self.choose(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.blocks, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Open).setText(_tr('Edytuj blok') if getattr(grid, 'editable', True) else _tr('Zaznacz blok'))
        self.anchor_button = buttons.addButton(_tr('Ustaw start tygodnia 1'), QDialogButtonBox.ButtonRole.ActionRole)
        self.anchor_button.setEnabled(bool(self.blocks.count()) and getattr(grid, 'editable', True))
        self.anchor_button.clicked.connect(lambda: self.choose_current('anchor'))
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_tr('Zamknij'))
        buttons.accepted.connect(lambda: self.choose_current())
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.blocks.currentItemChanged.connect(self._highlight_current)
        if self.blocks.count():
            self.blocks.setCurrentRow(0)

    def _highlight_current(self, item, previous):
        self.detail.selected_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.detail.update()

    def select_block(self, identity):
        for row in range(self.blocks.count()):
            if self.blocks.item(row).data(Qt.ItemDataRole.UserRole) == identity:
                self.blocks.setCurrentRow(row)
                return

    def choose_current(self, action='edit'):
        item = self.blocks.currentItem()
        if item:
            self.choose(item.data(Qt.ItemDataRole.UserRole), action)

    def choose(self, identity, action='edit'):
        self.selected_id = identity
        self.selected_action = action if getattr(self.detail.grid, 'editable', True) else 'select'
        self.accept()
