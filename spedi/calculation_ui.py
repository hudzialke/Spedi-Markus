from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QSortFilterProxyModel
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QTableView, QTabWidget, QVBoxLayout, QWidget,
)

from spedi.calculation_details import calculation_rows, calculation_text, duration, instant
from spedi.i18n import tr


class RowsModel(QAbstractTableModel):
    def __init__(self, headers, rows, parent=None):
        super().__init__(parent)
        self.headers, self.rows = headers, rows

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            return self.rows[index.row()][index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None


class CalculationDialog(QDialog):
    def __init__(self, name, result, parent=None):
        super().__init__(parent)
        self.name, self.result = name, result
        self.setWindowTitle(tr('Wyliczenie terminów')+' · '+name)
        self.resize(1080, 620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for label, value in (('W domu / bazie najpóźniej', instant(result.return_by)),
                             ('Odpoczynek z rekompensatami najpóźniej', instant(result.start_by)),
                             ('Kolejny OT — granica 6×24 h', instant(result.weekly_start_by)),
                             ('Odpoczynek w domu', duration(result.return_rest_minutes) if result.return_rest_minutes else '—')):
            form.addRow(tr(label), QLabel(value))
        layout.addLayout(form)
        reason = QLabel(result.return_reason+'\n'+result.reason)
        reason.setWordWrap(True)
        layout.addWidget(reason)
        search = QLineEdit()
        search.setPlaceholderText(tr('Filtruj datę, rodzaj odpoczynku lub rekompensatę…'))
        layout.addWidget(search)
        tabs = QTabWidget()
        rest_rows, event_rows = calculation_rows(result)
        for title, headers, rows in (
            ('Odpoczynki źródłowe', ('Od', 'Do', 'Czas', 'Podstawa', 'Zaliczona rekompensata', 'Kraj startu'), rest_rows),
            ('Historia rekompensat', ('Zdarzenie', 'Odpoczynek od / powstanie', 'Źródłowy OT od', 'Rekompensata', 'Termin', 'Przypisany tydzień od'), event_rows),
        ):
            table = QTableView()
            model = RowsModel([tr(header) for header in headers], rows, table)
            proxy = QSortFilterProxyModel(table)
            proxy.setSourceModel(model)
            proxy.setFilterKeyColumn(-1)
            proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            search.textChanged.connect(proxy.setFilterFixedString)
            table.setModel(proxy)
            table.setAlternatingRowColors(True)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            table.horizontalHeader().setDefaultSectionSize(175)
            table.horizontalHeader().setStretchLastSection(True)
            table.verticalHeader().setVisible(False)
            tabs.addTab(table, tr(title)+f' ({len(rows)})')
        layout.addWidget(tabs, 1)
        note = QLabel(tr('Wyliczenie dotyczy zapisanego widoku Zmodyfikowany. Przypisany tydzień pochodzi z planu powrotu, jeśli znana jest wystarczająca historia. '
                         'Pozostałe terminy są bazowe. Spłata wskazuje początek odpoczynku, przy którym ją zaliczono. Wyliczenie obejmuje również plany.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        save = buttons.addButton(tr('Zapisz wyliczenie TXT…'), QDialogButtonBox.ButtonRole.ActionRole)
        save.clicked.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(self, tr('Zapisz wyliczenie'), 'wyliczenie-terminow.txt', 'TXT (*.txt)')
        if path:
            try:
                Path(path).write_text(calculation_text(self.name, self.result), encoding='utf-8')
            except OSError as exc:
                QMessageBox.warning(self, tr('Nie zapisano wyliczenia'), str(exc))
