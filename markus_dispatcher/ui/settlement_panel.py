from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import QThread, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QGroupBox, QLineEdit, QLabel, QPushButton, QComboBox, QMessageBox, QFileDialog,
    QInputDialog, QDialog, QDialogButtonBox, QProgressDialog)

from markus_dispatcher.core import google_connection, settlement
from markus_dispatcher.core.google_sheets import Sheets, ConflictError
from markus_dispatcher.data import settlement_store, monthly_settlements


class Task(QThread):
    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation()
        except Exception as exc:
            self.error = exc


class WaitingDialog(QProgressDialog):
    def reject(self):
        # An in-flight write must finish before the job or its target can change.
        pass

    def closeEvent(self, event):
        event.ignore()


def run_task(parent, label, operation):
    progress = WaitingDialog(label, '', 0, 0, parent)
    progress.setWindowTitle(_tr('Rozliczenie — Google Sheets'))
    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
    progress.setCancelButton(None)
    progress.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    progress.setMinimumDuration(0)
    task = Task(operation, progress)
    task.finished.connect(progress.accept)
    task.start()
    progress.exec()
    task.wait()
    if task.error:
        raise task.error
    return task.result


class SettlementPanel(QGroupBox):
    def __init__(self, pane):
        super().__init__(_tr('Rozliczenie frachtu'), pane)
        self.pane = pane
        self.job_id = None
        self.file_id = ''
        self.binding = {}
        self._target_month = None
        self._loading_target = False
        self.setCheckable(True)
        self.setChecked(True)
        layout = QVBoxLayout(self)
        self.body = QWidget()
        layout.addWidget(self.body)
        self.toggled.connect(self.body.setVisible)
        body = QVBoxLayout(self.body)
        form = QFormLayout()
        self.fields = {}
        for key, label in [('load_place', _tr('Załadunek — kod pocztowy i kraj')),
                           ('unload_place', _tr('Rozładunek — kod pocztowy i kraj')),
                           ('empty_km', _tr('Dolot (km)')), ('loaded_km', _tr('Ładowne (km)')),
                           ('price', _tr('Stawka (EUR)')), ('costs', _tr('Koszty dodatkowe (EUR)')),
                           ('notes', _tr('Uwagi / opis kosztów'))]:
            edit = QLineEdit()
            if key in ('load_place', 'unload_place'):
                edit.setPlaceholderText(_tr('np. 00-001 PL — potwierdź na podstawie adresu'))
            elif key not in ('notes',):
                edit.setPlaceholderText(_tr('Wpisz wartość; brak kosztu lub kilometrów wpisz jako 0'))
            self.fields[key] = edit
            form.addRow(label, edit)
            edit.textChanged.connect(self._totals)
        body.addLayout(form)
        self.totals = QLabel(_tr('Uzupełnij kilometry i stawkę.'))
        body.addWidget(self.totals)
        hint = QLabel(_tr('Daty pochodzą ze zlecenia. Kwoty są prywatne — nie trafiają do instrukcji kierowcy.'))
        hint.setWordWrap(True)
        body.addWidget(hint)
        connection = QHBoxLayout()
        for label, handler in [(_tr('Konfiguracja Google'), self._configure), (_tr('Połącz z Google'), self._connect),
                               (_tr('Odłącz konto'), self._disconnect)]:
            self._button(connection, label, handler)
        connection.addStretch()
        body.addLayout(connection)
        target = QHBoxLayout()
        self.tabs = QComboBox()
        self.tabs.setMinimumWidth(180)
        target.addWidget(self.tabs, 1)
        self._button(target, _tr('Arkusz miesiąca…'), self._pick)
        self._button(target, _tr('Nowy arkusz miesiąca'), self._create)
        self._button(target, _tr('Dodaj zakładkę kierowcy'), self._add_tab)
        body.addLayout(target)
        actions = QHBoxLayout()
        self._button(actions, _tr('Zapisz rozliczenie'), self._save)
        self._button(actions, _tr('Podgląd'), self._preview)
        self.publish_btn = self._button(actions, _tr('Wyślij do arkusza'), self._publish)
        self._button(actions, _tr('Otwórz arkusz'), self._open)
        actions.addStretch()
        body.addLayout(actions)
        self.status = QLabel(_tr('Nie wysłano do Google Sheets.'))
        self.status.setWordWrap(True)
        body.addWidget(self.status)
        self.tabs.currentIndexChanged.connect(lambda _index: self._guard(self._remember_tab))

    def _button(self, layout, title, handler):
        button = QPushButton(title)
        button.clicked.connect(lambda checked=False: self._guard(handler))
        layout.addWidget(button)
        return button

    def _guard(self, handler):
        try:
            handler()
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, _tr('Rozliczenie'), str(exc))
        except Exception:
            # OAuth and HTTP exceptions can contain tokens or private response bodies.
            QMessageBox.warning(self, _tr('Rozliczenie'), _tr('Operacja nie powiodła się. Sprawdź konfigurację Google, dostęp do sieci i zaloguj się ponownie. Dane lokalne pozostały zapisane.'))

    def draft(self):
        return {**{key: edit.text() for key, edit in self.fields.items()},
                'file_id': self.file_id, 'sheet_id': self.tabs.currentData(),
                'sheet_title': self.tabs.currentText(), 'target_month': self._target_month}

    def save_draft(self, job_id):
        settlement_store.save_draft(job_id, self.draft())
        self.job_id = job_id

    def load_job(self, job_id):
        self.job_id = job_id
        record = settlement_store.load(job_id) if job_id else {'draft': {}, 'binding': {}}
        draft = record['draft']
        self._target_month = draft.get('target_month')
        self._loading_target = True
        self.binding = record['binding']
        for key, edit in self.fields.items():
            edit.setText(draft.get(key, ''))
        self.file_id = self.binding.get('file_id', draft.get('file_id', ''))
        self.tabs.clear()
        sid = self.binding.get('sheet_id', draft.get('sheet_id'))
        if sid is not None:
            self.tabs.addItem(self.binding.get('title', draft.get('sheet_title', _tr('Zakładka'))), sid)
        self._loading_target = False
        self._status()

    def _status(self):
        self.publish_btn.setText(_tr('Aktualizuj w arkuszu') if self.binding else _tr('Wyślij do arkusza'))
        self.tabs.setEnabled(not bool(self.binding))
        if self.binding.get('sent_at'):
            self.status.setText(f"{_tr('Zapisano w Google: ')}{self.binding.get('title', '')} · {self.binding['sent_at']}")
        elif self.binding:
            self.status.setText(_tr('Wysyłka wymaga sprawdzenia. Ponowienie sprawdzi powiązanie przed dopisaniem.'))
        else:
            month = self.month_key() or _tr('brak daty rozładunku')
            self.status.setText(f"{_tr('Arkusz miesiąca ')}{month} · " + self.tabs.currentText() if self.file_id else f"{_tr('Miesiąc ')}{month}{_tr(': przypisz arkusz przyciskiem „Arkusz miesiąca…”.')}")

    def _totals(self):
        if not hasattr(self, 'totals'):
            return
        try:
            km = settlement.amount(self.fields['empty_km'].text(), _tr('Dolot')) + settlement.amount(self.fields['loaded_km'].text(), _tr('Ładowne'))
            price = settlement.amount(self.fields['price'].text(), _tr('Stawka'))
            costs = settlement.amount(self.fields['costs'].text(), _tr('Koszty'))
            rate = f'{price / km:.2f} EUR/km' if km else _tr('brak kilometrów do wyliczenia EUR/km')
            self.totals.setText(f"{_tr('Razem: ')}{km:.2f} km · {rate} · Suma netto: {price - costs:.2f} EUR")
        except ValueError:
            self.totals.setText(_tr('Uzupełnij kilometry, stawkę i koszty (brak wartości nie oznacza zera).'))

    def _ensure_saved(self):
        if self.pane._driver_id is None:
            raise ValueError(_tr('Wybierz kierowcę przed zapisaniem rozliczenia.'))
        self.pane._save(show_message=False)
        if self.pane._job_id is None:
            raise ValueError(_tr('Najpierw zapisz zlecenie.'))
        self.job_id = self.pane._job_id
        return self.job_id

    def _save(self):
        self._ensure_saved()
        self.status.setText(_tr('Zapisano rozliczenie lokalnie. Aby wysłać zmiany, użyj przycisku wysyłki/aktualizacji.'))

    def _preview(self):
        values = settlement.payload(self.draft(), self.pane.collect())
        QMessageBox.information(self, _tr('Podgląd rozliczenia'), settlement.preview(values, self.binding.get('last')))

    def _configure(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(_tr('Dostęp Dispatchera do Google Sheets'))
        form = QFormLayout(dialog)
        info = QLabel(_tr('W projekcie Google Cloud włącz Sheets API, Drive API i Google Picker API.\nUtwórz klienta OAuth typu „Aplikacja komputerowa” i pobierz JSON.\nKlucz Picker i numer projektu muszą pochodzić z tego samego projektu.'))
        info.setWordWrap(True)
        form.addRow(info)
        path = QLineEdit()
        browse = QPushButton(_tr('Wybierz JSON klienta OAuth'))
        browse.clicked.connect(lambda: path.setText(QFileDialog.getOpenFileName(dialog, _tr('Klient OAuth'), '', 'JSON (*.json)')[0] or path.text()))
        form.addRow(browse, path)
        key, project = QLineEdit(), QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_tr('Klucz API Google Picker'), key)
        form.addRow(_tr('Numer projektu (cyfry)'), project)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            google_connection.configure(path.text(), key.text(), project.text())
            self.status.setText(_tr('Konfiguracja zapisana. Kliknij „Połącz z Google”.'))

    def _connect(self):
        run_task(self, _tr('Dokończ logowanie w przeglądarce (do 3 minut)…'), google_connection.connect)
        self.status.setText(_tr('Połączono z Google. Możesz wybrać lub utworzyć arkusz.'))

    def _disconnect(self):
        google_connection.disconnect()
        self.status.setText(_tr('Usunięto lokalne logowanie. Dostęp aplikacji możesz też cofnąć na koncie Google.'))

    def _target_allowed(self):
        if self.binding:
            raise ValueError(_tr('To zlecenie jest już powiązane z arkuszem. Nowe arkusze wybieraj przy niewysłanym zleceniu.'))

    def month_key(self):
        return monthly_settlements.month_of(self.pane.collect().unload_datetime)

    def _fill_target(self, file_id, tabs, selected=None):
        self._loading_target = True
        try:
            self.file_id = file_id
            self.tabs.clear()
            self.tabs.addItem(_tr('— wybierz zakładkę kierowcy —'), None)
            for tab in tabs:
                self.tabs.addItem(tab['title'], tab['sheetId'])
            if selected is not None:
                index = self.tabs.findData(selected)
                self.tabs.setCurrentIndex(max(0, index))
        finally:
            self._loading_target = False
        self._status()

    def apply_month_target(self):
        if self.binding:
            return
        month = self.month_key()
        if not month:
            return
        target = monthly_settlements.get(month)
        previous_month = self._target_month
        self._target_month = month
        if target:
            sid = target.get('drivers', {}).get(str(self.pane._driver_id))
            if sid is None:
                matches = [tab['sheetId'] for tab in target['tabs']
                           if tab['title'].strip().casefold() == self.pane._driver_label().strip().casefold()]
                sid = matches[0] if len(matches) == 1 else None
            self._fill_target(target['file_id'], target['tabs'], sid)
            if sid is not None:
                self._remember_tab()
        elif previous_month and previous_month != month:
            self._fill_target('', [])
        else:
            self._status()

    def _remember_tab(self):
        if self._loading_target or self.binding or self.pane._loading_history:
            return
        month, sid, driver_id = self.month_key(), self.tabs.currentData(), self.pane._driver_id
        if month and sid is not None and driver_id is not None:
            target = monthly_settlements.get(month)
            if target.get('file_id') == self.file_id:
                monthly_settlements.bind_driver(month, driver_id, self.file_id, sid)
                self._target_month = month
                if self.job_id:
                    self.save_draft(self.job_id)
        self._status()

    def _select(self, file_id, tabs, selected=None):
        month = self.month_key()
        if not month:
            raise ValueError(_tr('Uzupełnij datę rozładunku przed przypisaniem arkusza miesiąca.'))
        previous = monthly_settlements.get(month)
        if previous and previous['file_id'] != file_id:
            if QMessageBox.question(self, _tr('Arkusz miesiąca'), f"{_tr('Zmienić arkusz przypisany do miesiąca ')}{month}{_tr('?\nNowe zlecenia użyją nowego pliku. Już wysłane pozycje zachowają poprzedni plik.')}",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        monthly_settlements.assign(month, file_id, tabs)
        target = monthly_settlements.get(month)
        if selected is None:
            selected = target.get('drivers', {}).get(str(self.pane._driver_id))
        if selected is None:
            matches = [t['sheetId'] for t in tabs if t['title'].strip().casefold() == self.pane._driver_label().strip().casefold()]
            if len(matches) == 1:
                selected = matches[0]
            elif len(tabs) == 1:
                selected = tabs[0]['sheetId']
        self._target_month = month
        self._fill_target(file_id, tabs, selected)
        self._remember_tab()
        if self.job_id:
            self.save_draft(self.job_id)

    def _pick(self):
        self._target_allowed()
        file_id = run_task(self, _tr('Wybierz arkusz w przeglądarce (do 3 minut)…'), google_connection.pick_spreadsheet)
        if file_id:
            tabs = run_task(self, _tr('Odczytuję zakładki…'), lambda: Sheets().tabs(file_id))
            self._select(file_id, tabs)

    def _create(self):
        self._target_allowed()
        title, ok = QInputDialog.getText(self, _tr('Nowy arkusz'), _tr('Nazwa pliku rozliczenia miesiąca:'),
                                        text=_tr('Rozliczenie ') + self.pane.load_when.dateTime().toString('MM_yyyy'))
        if not ok or not title.strip():
            return
        tab, ok = QInputDialog.getText(self, _tr('Zakładka kierowcy'), _tr('Nazwa zakładki:'), text=self.pane._driver_label())
        if not ok or not tab.strip():
            return
        if QMessageBox.question(self, _tr('Utwórz arkusz'), f"{_tr('Utworzyć „')}{title}{_tr('” z zakładką „')}{tab}{_tr('”?\nPowstanie czysty wzór B–M z formułami i 1000 miejsc na frachty.')}",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        result = run_task(self, _tr('Tworzę nowy arkusz…'), lambda: Sheets().create(title, tab))
        self._select(result['spreadsheetId'], [s['properties'] for s in result['sheets']])

    def _add_tab(self):
        self._target_allowed()
        if not self.file_id:
            raise ValueError(_tr('Najpierw wybierz plik arkusza.'))
        title, ok = QInputDialog.getText(self, _tr('Nowa zakładka'), _tr('Nazwa zakładki kierowcy:'), text=self.pane._driver_label())
        if not ok or not title.strip():
            return
        file_id = self.file_id
        tab = run_task(self, _tr('Dodaję zakładkę…'), lambda: Sheets().add_tab(file_id, title))
        tabs = run_task(self, _tr('Odczytuję zakładki…'), lambda: Sheets().tabs(file_id))
        self._select(file_id, tabs, tab['sheetId'])

    def _publish(self):
        self.apply_month_target()
        values = settlement.payload(self.draft(), self.pane.collect())
        if self.binding.get('last'):
            old_date = self.binding['last'][3] + ' 00:00'
            if monthly_settlements.month_of(old_date) != self.month_key():
                raise ValueError(_tr('Wysłane rozliczenie należy do innego miesiąca. Aktualizacja nie przenosi go do innego arkusza; przywróć datę lub rozlicz przeniesienie osobno.'))
        if not self.file_id or self.tabs.currentData() is None:
            raise ValueError(_tr('Wybierz arkusz i zakładkę kierowcy.'))
        job_id = self._ensure_saved()
        file_id, sheet_id = self.file_id, self.tabs.currentData()
        message = f"{_tr('Zakładka: ')}{self.tabs.currentText()}\n\n" + settlement.preview(values, self.binding.get('last'))
        if QMessageBox.question(self, _tr('Zapis do Google Sheets'), message + _tr('\n\nZapisać te dane w arkuszu?'),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            try:
                result = run_task(self, _tr('Zapisuję rozliczenie…'), lambda: Sheets().publish(job_id, file_id, sheet_id, values))
            except ConflictError as conflict:
                remote = conflict.remote
                question = 'Dane zmieniono w Google Sheets.\nZmiany: arkusz → Dispatcher\n\n' + settlement.preview(values, remote)
                if QMessageBox.question(self, _tr('Konflikt zmian'), question + _tr('\n\nZastąpić wartości w arkuszu danymi z Dispatchera?'),
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
                result = run_task(self, _tr('Aktualizuję po sprawdzeniu konfliktu…'),
                                  lambda: Sheets().publish(job_id, file_id, sheet_id, values, overwrite_remote=remote))
            self.binding = result
        finally:
            self.binding = settlement_store.load(job_id)['binding']
            self._status()

    def _open(self):
        if not self.file_id:
            raise ValueError(_tr('Najpierw wybierz arkusz.'))
        file_id = Sheets.file_id(self.file_id)
        sid = self.tabs.currentData()
        QDesktopServices.openUrl(QUrl(f'https://docs.google.com/spreadsheets/d/{file_id}/edit#gid={sid if sid is not None else 0}'))
