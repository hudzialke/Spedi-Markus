
from spedi.i18n import tr as _tr
"""Weekly-rest list using the existing Spedi table/dialog controls."""
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QAbstractItemView, QToolBar,
)

from brudnopis_ot.core.display_tz import format_dt
from spedi.weekly_watch import COUNTRIES, WatchStore, calculate, country_code, history_revision
from spedi.weekly_watch_tasks import WatchTask

STATUS = {"yes": _tr("TAK"), "conditional": _tr("TAK WARUNKOWO"), "no": _tr("NIE — regularny"), "unknown": _tr("BRAK DANYCH")}
COLORS = {"yes": "#128c7e", "conditional": "#8a5700", "no": "#be123c", "unknown": "#5a6672"}


def instant(value):
    return format_dt(value, "Europe/Warsaw") if value else "—"


class SortItem(QTableWidgetItem):
    def __lt__(self, other):
        return self.data(Qt.ItemDataRole.UserRole) < other.data(Qt.ItemDataRole.UserRole)


class DriverOptionsDialog(QDialog):
    def __init__(self, profile, scenario, options, parent=None, result=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Warunki skrócenia — ") + (profile.driver_name or profile.title))
        self.resize(640, 300)
        self.options = options
        self.scenario = scenario
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow(_tr("Kraj siedziby firmy (z Brudnopisu)"), QLabel(scenario.employer_country))
        self.residence = QLineEdit(options.residence_country)
        self.residence.setPlaceholderText(_tr("Kod kraju, np. PL, UA, DE"))
        self.residence.setMaxLength(3)
        form.addRow(_tr("Kraj zamieszkania kierowcy"), self.residence)
        self.international = QCheckBox(_tr("Międzynarodowy przewóz rzeczy (warunek wyjątku 2× skrócony)"))
        self.international.setChecked(options.international_goods)
        form.addRow(self.international)
        result = result if result is not None else calculate(scenario, options)
        period = result.history_from.strftime("%d.%m.%Y") if result.history_from else "—"
        self.history = QCheckBox(_tr("Potwierdzam kompletną historię odpoczynków od ") + period)
        self.history.setChecked(options.verified_revision == history_revision(scenario))
        form.addRow(self.history)
        layout.addLayout(form)
        note = QLabel(_tr("Potwierdzenie obejmuje odpoczynki wymagane i dodatkowe, także przed startem tygodnia 1. "
                      "Zmiana bloków wymaga ponownego potwierdzenia. Kraje rozpoczęcia odpoczynków uzupełnia się w edytorze bloków."))
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(_tr("Zapisz"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_tr("Anuluj"))
        buttons.accepted.connect(self._accept_options)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_options(self):
        value = self.residence.text().strip()
        if value and country_code(value) not in COUNTRIES:
            QMessageBox.information(self, _tr("Kraj zamieszkania"), _tr("Podaj kod kraju, np. PL, UA, DE; pozostaw puste, jeśli nie jest znany."))
            return
        self.accept()

    def value(self):
        return replace(self.options, residence_country=country_code(self.residence.text()),
                       international_goods=self.international.isChecked(),
                       verified_revision=history_revision(self.scenario) if self.history.isChecked() else "")


class WeeklyWatchDialog(QDialog):
    def __init__(self, profile_store, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(_tr("Lista OT — wybrani kierowcy"))
        self.resize(1460, 660)
        self.profile_store = profile_store
        self.settings = WatchStore(profile_store.path)
        self._loading = False
        self._worker = None
        self._generation = 0
        self._pending = False
        self._closing = None
        self._restore_key = None
        self.profiles, self.results, self.scenarios, self.options = {}, {}, {}, {}
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self._start_worker)
        self._version_connection = sqlite3.connect(profile_store.path.resolve().as_uri() + '?mode=ro', uri=True)
        self._db_version = self._version_connection.execute('PRAGMA data_version').fetchone()[0]
        self._version_timer = QTimer(self)
        self._version_timer.setInterval(1000)
        self._version_timer.timeout.connect(self._check_database)
        self._version_timer.start()
        layout = QVBoxLayout(self)
        intro = QLabel(_tr("Ostateczny termin przyjazdu do domu / bazy i kolejnego OT • czas polski (Europe/Warsaw)"))
        layout.addWidget(intro)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(_tr("Szukaj kierowcy lub numeru karty…"))
        self.search.textChanged.connect(self._filter)
        bar.addWidget(self.search, 1)
        self.only_selected = QCheckBox(_tr("Tylko wybrani"))
        self.only_selected.setChecked(True)
        self.only_selected.toggled.connect(self._filter)
        bar.addWidget(self.only_selected)
        for text, slot in ((_tr("Wybierz kierowców…"), self._choose), (_tr("Warunki skrócenia…"), self._options), (_tr("Odśwież"), self.reload)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([_tr("Na liście"), _tr("Kierowca / profil"), _tr("Start odpoczynku\nnajpóźniej"), _tr("Granica 6×24 h\nstart OT 45 h"), _tr("Rekompensata\nprzed 45 h"), _tr("Skrócony na ten termin"), _tr("Ostatni blok\n(dane lub plan)"), _tr("Powód"), _tr("W domu / bazie\nnajpóźniej"), _tr("Odpoczynek w domu\n45 h + rekompensaty")])
        self.table.horizontalHeader().moveSection(8, 2)
        self.table.horizontalHeader().moveSection(9, 3)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._checked)
        self.table.itemSelectionChanged.connect(self._details)
        self.table.cellDoubleClicked.connect(lambda *_: self._options())
        layout.addWidget(self.table, 1)
        self.count = QLabel()
        layout.addWidget(self.count)
        progress_bar = QHBoxLayout()
        self.progress = QLabel()
        progress_bar.addWidget(self.progress, 1)
        self.cancel_button = QPushButton(_tr('Anuluj obliczenia'))
        self.cancel_button.clicked.connect(self._cancel_calculation)
        self.cancel_button.setEnabled(False)
        progress_bar.addWidget(self.cancel_button)
        layout.addLayout(progress_bar)
        self.detail = QLabel(_tr("Zaznacz wiersz, aby zobaczyć uzasadnienie. Dwuklik otwiera warunki skrócenia."))
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(48)
        layout.addWidget(self.detail)
        note = QLabel(_tr("Obliczenia obejmują zapisany widok Zmodyfikowany, również plany i historię sprzed tygodnia 1. "
                      "Start tygodnia 1 oznacza początek trasy po regularnym OT w domu / bazie; po powrocie ustaw nowy start. "
                      "TAK dotyczy podanego terminu i znanej historii. WARUNKOWO wymaga startu za granicą; "
                      "wcześniejszy start może zmienić ocenę. Miniony termin pozostaje widoczny."))
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_tr("Zamknij"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.reload()

    def reload(self, *_, keep_rows=False):
        if self._closing is not None:
            return
        self._generation += 1
        self._restore_key = self._current_key() or self._restore_key
        self._pending = True
        self._failed_request = False
        self._row_errors = 0
        self._loading = True
        self.results, self.scenarios, self.options = {}, {}, {}
        if keep_rows:
            for row in range(self.table.rowCount()):
                for col in range(2, 10):
                    self.table.item(row, col).setText(_tr('Oczekuje na obliczenie') if col == 5 else '…')
                    self.table.item(row, col).setForeground(QColor(COLORS['unknown']))
                    self.table.item(row, col).setToolTip('')
        else:
            self.table.setRowCount(0)
        self._loading = False
        self.progress.setText(_tr('Wczytuję listę i obliczam widocznych kierowców…'))
        self.detail.setText(_tr('Wyniki pojawią się po zakończeniu obliczeń.'))
        self.cancel_button.setEnabled(True)
        if self._worker is not None:
            self._worker.requestInterruption()
        self._refresh_timer.start()

    def _start_worker(self):
        if self._worker is not None or not self._pending or self._closing is not None:
            return
        self._pending = False
        worker = WatchTask(self.profile_store.path, self._generation,
                           self.only_selected.isChecked(), self.search.text().casefold().strip(), self)
        self._worker = worker
        worker.profiles_ready.connect(self._profiles_ready)
        worker.result_ready.connect(self._result_ready)
        worker.row_failed.connect(self._row_failed)
        worker.failed.connect(self._failed)
        worker.invalidated.connect(self._invalidated)
        worker.finished.connect(self._finished)
        worker.start()

    def _profiles_ready(self, generation, profiles):
        if generation != self._generation or self._closing is not None:
            return
        self._loading = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.profiles = {profile.profile_key: profile for profile, _ in profiles}
        self.options = {profile.profile_key: options for profile, options in profiles}
        for profile, options in profiles:
            row = self.table.rowCount()
            self.table.insertRow(row)
            checked = QTableWidgetItem()
            checked.setData(Qt.ItemDataRole.UserRole, profile.profile_key)
            checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            checked.setCheckState(Qt.CheckState.Checked if options.selected else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, checked)
            identity = _tr('ręczny') if profile.source_kind == 'template' else (profile.card_number or profile.vehicle_plate or 'DDD')
            self.table.setItem(row, 1, QTableWidgetItem(f'{profile.driver_name or profile.title} · {identity}'))
            for col in range(2, 10):
                item = SortItem('…') if col in (2, 3, 6, 8) else QTableWidgetItem('…')
                if col in (2, 3, 6, 8):
                    item.setData(Qt.ItemDataRole.UserRole, '9999')
                self.table.setItem(row, col, item)
            self.table.item(row, 5).setText(_tr('Oczekuje na obliczenie'))
        for col, width in enumerate((76, 200, 150, 155, 110, 190, 150, 300, 170, 190)):
            self.table.setColumnWidth(col, width)
        self.table.setSortingEnabled(True)
        self.table.sortItems(8, Qt.SortOrder.AscendingOrder)
        self._filter(schedule=False)
        self._loading = False
        row = self._row(self._restore_key)
        if row is not None:
            self.table.selectRow(row)

    def _row(self, key):
        return next((row for row in range(self.table.rowCount())
                     if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == key), None)

    def _result_ready(self, generation, key, payload):
        if generation != self._generation or self._closing is not None:
            return
        row = self._row(key)
        if row is None:
            return
        result, scenario, options = payload
        self.results[key], self.scenarios[key], self.options[key] = result, scenario, options
        self._loading = True
        self.table.setSortingEnabled(False)
        values = [instant(result.start_by), instant(result.weekly_start_by),
                  f"{result.compensation_minutes // 60}:{result.compensation_minutes % 60:02d}",
                  STATUS[result.reduced], instant(result.last_activity), result.reason]
        for col, value in enumerate(values, 2):
            item = SortItem(value) if col in (2, 3, 6) else QTableWidgetItem(value)
            if col in (2, 3, 6):
                dt = {2: result.start_by, 3: result.weekly_start_by, 6: result.last_activity}[col]
                item.setData(Qt.ItemDataRole.UserRole, dt.isoformat() if dt else "9999")
            item.setToolTip(result.reason)
            self.table.setItem(row, col, item)
        self.table.item(row, 5).setForeground(QColor(COLORS[result.reduced]))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if result.start_by and result.start_by < now:
            self.table.item(row, 2).setForeground(QColor(COLORS["no"]))
            self.table.item(row, 2).setToolTip(_tr("Termin minął według zapisanej historii. ") + result.reason)
        home = SortItem(instant(result.return_by) if result.return_by else _tr("BRAK DANYCH"))
        home.setData(Qt.ItemDataRole.UserRole, result.return_by.isoformat() if result.return_by else "9999")
        home.setToolTip(result.return_reason)
        if result.return_by and result.return_by < now:
            home.setForeground(QColor(COLORS["no"]))
            home.setToolTip(_tr("Termin powrotu minął według zapisanej trasy. ") + result.return_reason)
        self.table.setItem(row, 8, home)
        comp = result.return_compensation_minutes
        total = result.return_rest_minutes
        duration = QTableWidgetItem(f"{total // 60}:{total % 60:02d} (45:00 + {comp // 60}:{comp % 60:02d})" if total else "—")
        duration.setToolTip(result.return_reason)
        self.table.setItem(row, 9, duration)
        self.table.setSortingEnabled(True)
        self._loading = False
        self.progress.setText(_tr('Obliczono widocznych kierowców: ') + str(len(self.results)))
        self._details()

    def _row_failed(self, generation, key, message):
        if generation != self._generation:
            return
        row = self._row(key)
        if row is not None:
            self._row_errors += 1
            self.table.item(row, 5).setText(_tr('BŁĄD DANYCH'))
            self.table.item(row, 7).setText(message)

    def _failed(self, generation, message):
        if generation == self._generation:
            self._failed_request = True
            self.progress.setText(message)

    def _invalidated(self, generation):
        if generation == self._generation:
            self.reload()

    def _finished(self):
        worker = self._worker
        self._worker = None
        worker.deleteLater()
        if self._closing is not None:
            result, self._closing = self._closing, None
            self.done(result)
        elif self._pending:
            self._start_worker()
        else:
            self.cancel_button.setEnabled(False)
            if worker.generation == self._generation and not self._failed_request:
                if self._row_errors:
                    self.progress.setText(_tr('Zakończono. Sprawdź wiersze oznaczone błędem.'))
                elif self.results:
                    self.progress.setText(_tr('Obliczono widocznych kierowców: ') + str(len(self.results)))
                else:
                    self.progress.setText(_tr('Brak widocznych kierowców do obliczenia.'))

    def _cancel_calculation(self):
        self._generation += 1
        self._pending = False
        self._refresh_timer.stop()
        if self._worker is not None:
            self._worker.requestInterruption()
        self.cancel_button.setEnabled(False)
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if key not in self.results:
                self.table.item(row, 5).setText(_tr('Obliczenia anulowane'))
        self.progress.setText(_tr('Obliczenia anulowane. Kliknij „Odśwież”, aby kontynuować.'))

    def _check_database(self):
        if self._version_connection is None or self._closing is not None:
            return
        try:
            version = self._version_connection.execute('PRAGMA data_version').fetchone()[0]
        except sqlite3.Error:
            self._cancel_calculation()
            self.results.clear()
            self.table.setRowCount(0)
            self.progress.setText(_tr('Utracono dostęp do bazy. Otwórz listę ponownie.'))
            return
        if version != self._db_version:
            self._db_version = version
            self.reload()

    def done(self, result):
        self._refresh_timer.stop()
        self._version_timer.stop()
        self._pending = False
        self._generation += 1
        if self._worker is not None:
            self._closing = result
            self._worker.requestInterruption()
            return
        if self._version_connection is not None:
            self._version_connection.close()
            self._version_connection = None
        super().done(result)

    def _current_key(self):
        row = self.table.currentRow() if hasattr(self, "table") else -1
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _checked(self, item):
        if self._loading or item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        try:
            self.settings.save(key, replace(self.settings.get(key), selected=item.checkState() == Qt.CheckState.Checked))
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            QMessageBox.critical(self, _tr("Lista OT"), f"{_tr('Nie udało się zapisać wyboru: ')}{exc}")
            self.reload()
            return
        self._filter()

    def _filter(self, *_, schedule=True):
        text = self.search.text().casefold().strip()
        selected = visible = 0
        for row in range(self.table.rowCount()):
            chosen = self.table.item(row, 0).checkState() == Qt.CheckState.Checked
            selected += chosen
            show = (chosen or not self.only_selected.isChecked()) and text in self.table.item(row, 1).text().casefold()
            self.table.setRowHidden(row, not show)
            visible += show
        self.count.setText(f"{_tr('Wybrano: ')}{selected} • Widoczne: {visible} • Wszystkie profile: {self.table.rowCount()}" +
                           (_tr(" • Kliknij „Wybierz kierowców…”, aby dodać osoby do listy.") if not selected else ""))

        if schedule and not self._loading:
            self.reload(keep_rows=True)

    def _choose(self):
        self.only_selected.setChecked(False)
        self.search.clear()
        self.table.setFocus()

    def _details(self):
        key = self._current_key()
        result = self.results.get(key) if hasattr(self, "results") else None
        if result:
            self.detail.setText(result.return_reason + "\n" + result.reason)

    def _options(self):
        key = self._current_key()
        if key not in self.profiles:
            self.detail.setText(_tr("Najpierw zaznacz wiersz kierowcy."))
            return
        try:
            if key not in self.results:
                self.detail.setText(_tr('Poczekaj na obliczenie wybranego kierowcy.'))
                return
            scenario = self.scenarios[key]
            dialog = DriverOptionsDialog(self.profiles[key], scenario, self.options[key], self, result=self.results[key])
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.settings.save(key, dialog.value())
                self.reload()
        except Exception as exc:
            QMessageBox.critical(self, _tr("Warunki skrócenia"), str(exc))


def open_watchlist(brudnopis):
    # A failed autosave must not silently show an older version as current.
    if brudnopis._dirty and brudnopis._ddd_profile is not None:
        saved = brudnopis._persist_template() if brudnopis._is_template() else brudnopis._persist_ddd()
        if not saved:
            return
    try:
        WeeklyWatchDialog(brudnopis._ddd_store, brudnopis).exec()
    except Exception as exc:
        QMessageBox.critical(brudnopis, _tr("Lista OT"), f"{_tr('Nie udało się otworzyć listy: ')}{exc}")


def install_watchlist(brudnopis):
    action = QAction(_tr("Lista OT…"), brudnopis)
    action.setObjectName("WeeklyWatchAction")
    action.setToolTip(_tr("Wybrani kierowcy: termin powrotu do domu / bazy, rekompensaty i kolejny OT"))
    action.triggered.connect(lambda: open_watchlist(brudnopis))
    toolbars = brudnopis.findChildren(QToolBar, options=Qt.FindChildOption.FindDirectChildrenOnly)
    if toolbars:
        toolbar = toolbars[0]
        toolbar.insertAction(toolbar.actions()[0] if toolbar.actions() else None, action)
    else:
        toolbar = QToolBar(_tr("Lista kierowców OT"), brudnopis)
        toolbar.setMovable(False)
        toolbar.addAction(action)
        brudnopis.addToolBar(toolbar)
    return action
