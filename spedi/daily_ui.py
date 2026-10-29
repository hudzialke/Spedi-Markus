"""Approved split-list daily overview, populated off the GUI thread."""
from datetime import datetime, timezone
from html import escape

from PySide6.QtCore import Qt, QTimer, QSortFilterProxyModel, QModelIndex
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTableView, QVBoxLayout,
)

from spedi.background import BackgroundTask
from spedi.calculation_ui import RowsModel
from spedi.daily_overview import collect_actions, urgency
from spedi.weekly_watch import WARSAW
from spedi.i18n import tr


class ActionRows(RowsModel):
    def __init__(self, category, parent=None):
        super().__init__([tr('Termin · czas polski'), tr('Kierowca' if category == 'drivers' else 'Zlecenie'), tr('Do zrobienia')], [], parent)
        self.items = []

    def replace(self, items, now):
        self.beginResetModel()
        self.items = items
        self.rows = []
        for item in items:
            _, status = urgency(item, now)
            when = (item.deadline.replace(tzinfo=timezone.utc).astimezone(WARSAW).strftime('%d.%m.%Y %H:%M')
                    if item.deadline else '—')
            self.rows.append((status+'\n'+when, item.subject, item.title))
        self.endResetModel()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.UserRole:
            return self.items[index.row()]
        if index.isValid() and role == Qt.ItemDataRole.ToolTipRole:
            item = self.items[index.row()]
            return escape(item.subject+' · '+item.title+'\n'+item.detail)
        return super().data(index, role)


class DailyOverviewDialog(QDialog):
    def __init__(self, profile_path, dispatcher_path, archive_root, parent=None, *, loader=collect_actions, now=None):
        super().__init__(parent)
        self.paths, self.loader, self.now = (profile_path, dispatcher_path, archive_root), loader, now
        self._worker = None
        self._pending = False
        self._closing = None
        self._loaded = False
        self.chosen = self._current = None
        self.setWindowTitle(tr('Co wymaga uwagi dzisiaj'))
        self.resize(1200, 690)
        layout = QVBoxLayout(self)
        title = QLabel(tr('Co wymaga uwagi dzisiaj'))
        title.setObjectName('BrandTitle')
        layout.addWidget(title)
        self.status = QLabel(tr('Odczytywanie lokalnych danych…'))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('Szukaj kierowcy, zlecenia lub zadania…'))
        self.search.textChanged.connect(self._filter)
        bar.addWidget(self.search, 1)
        refresh = QPushButton(tr('Odśwież'))
        refresh.clicked.connect(self.reload)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        columns = QHBoxLayout()
        self.tables, self.models, self.proxies = {}, {}, {}
        self.counts = {}
        for category, label in (('drivers', 'Kierowcy i odpoczynki'), ('documents', 'Zlecenia i dokumenty')):
            column = QVBoxLayout()
            heading = QLabel(tr(label))
            self.counts[category] = heading
            column.addWidget(heading)
            table = QTableView()
            table.setAccessibleName(tr(label))
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.verticalHeader().setVisible(False)
            table.verticalHeader().setDefaultSectionSize(48)
            model = ActionRows(category, table)
            proxy = QSortFilterProxyModel(table)
            proxy.setSourceModel(model)
            proxy.setFilterKeyColumn(-1)
            proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            table.setModel(proxy)
            table.horizontalHeader().resizeSection(0, 165)
            table.horizontalHeader().resizeSection(1, 150)
            table.horizontalHeader().setStretchLastSection(True)
            table.selectionModel().selectionChanged.connect(lambda *_args, view=table: self._select(view))
            table.doubleClicked.connect(lambda *_args: self._open())
            self.tables[category], self.models[category], self.proxies[category] = table, model, proxy
            column.addWidget(table, 1)
            columns.addLayout(column, 1)
        layout.addLayout(columns, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(105)
        self.detail.setPlaceholderText(tr('Zaznacz sprawę, aby zobaczyć szczegóły.'))
        layout.addWidget(self.detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.open_button = buttons.addButton(tr('Otwórz wybraną sprawę'), QDialogButtonBox.ButtonRole.ActionRole)
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.reload)
        self._refresh_timer.start(0)

    def _filter(self, text):
        for table in self.tables.values():
            table.clearSelection()
            table.setCurrentIndex(QModelIndex())
        for proxy in self.proxies.values():
            proxy.setFilterFixedString(text)
        self._current = None
        self.open_button.setEnabled(False)
        self.detail.clear()

    def _select(self, table):
        selected = table.selectionModel().selectedRows()
        if not selected:
            return
        item = selected[0].data(Qt.ItemDataRole.UserRole)
        for other in self.tables.values():
            if other is not table:
                other.clearSelection()
        self._current = item
        self.detail.setPlainText(item.subject+' · '+item.title+'\n'+item.detail)
        self.open_button.setEnabled(True)

    def _open(self):
        if self._current and self._worker is None:
            self.chosen = self._current
            self.accept()

    def reload(self):
        if self._closing is not None:
            return
        if self._worker:
            self._pending = True
            self._worker.requestInterruption()
            return
        self._current = None
        self.open_button.setEnabled(False)
        for model in self.models.values():
            model.replace([], self.now or datetime.now(timezone.utc))
        self.status.setText(tr('Odczytywanie lokalnych danych…'))
        self.detail.clear()
        self._worker = BackgroundTask(lambda task: self.loader(*self.paths, now=self.now,
            cancelled=task.isInterruptionRequested), self)
        self._worker.finished.connect(self._finished)
        self._worker.start()

    def _finished(self):
        worker, self._worker = self._worker, None
        worker.deleteLater()
        if self._closing is not None:
            result, self._closing = self._closing, None
            self.done(result)
            return
        if self._pending:
            self._pending = False
            self.reload()
            return
        self._loaded = True
        if worker.error:
            self.status.setText(tr('Nie odczytano panelu dnia.'))
            self.detail.setPlainText(str(worker.error))
            return
        items, warnings = worker.result
        now = self.now or datetime.now(timezone.utc)
        for category, model in self.models.items():
            selected = [item for item in items if item.category == category]
            model.replace(selected, now)
            label = 'Kierowcy i odpoczynki' if category == 'drivers' else 'Zlecenia i dokumenty'
            self.counts[category].setText(tr(label)+f' ({len(selected)})')
        stamp = now.astimezone(WARSAW).strftime('%d.%m.%Y %H:%M')
        text = tr('Odświeżono: ')+stamp+' · '+tr('Terminy do 7 dni oraz zaległe sprawy')
        if warnings:
            text += ' · '+tr('Źródła wymagające sprawdzenia: ')+str(len(warnings))
            self.detail.setPlainText('\n'.join(warnings))
        elif not items:
            text += ' · '+tr('Brak spraw w tym zakresie.')
        self.status.setText(text)

    def done(self, result):
        self._refresh_timer.stop()
        self._pending = False
        if self._worker is not None:
            self._closing = result
            self._worker.requestInterruption()
            return
        super().done(result)

    def closeEvent(self, event):
        if self._worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
