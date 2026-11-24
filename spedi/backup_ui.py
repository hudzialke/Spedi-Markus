"""Small data-management dialogs using the application's existing Qt controls."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QLineEdit, QGroupBox, QTabWidget, QWidget,
)

from spedi.background import run_background
from spedi.i18n import tr


def _stamp(value):
    return datetime.fromisoformat(value).astimezone().strftime('%d.%m.%Y %H:%M:%S')


class BackupDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle(tr('Backup danych'))
        self.resize(900, 660)
        layout = QVBoxLayout(self)
        description = QLabel(tr('All in: zapisane bazy wszystkich modułów, archiwa DDD, załączniki, scenariusze i raporty. Dane logowania do usług pozostają poza kopią.'))
        description.setWordWrap(True)
        layout.addWidget(description)
        group = QGroupBox(tr('All in — pełna kopia 7z'))
        form = QVBoxLayout(group)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(tr('Folder zapisu:')))
        self.folder = QLineEdit(str(QSettings('Spedi', 'Backup').value('folder', str(manager.directory))))
        self.folder.setAccessibleName(tr('Folder zapisu kopii'))
        folder_row.addWidget(self.folder, 1)
        browse = QPushButton(tr('Wybierz folder…'))
        browse.clicked.connect(self.choose_folder)
        folder_row.addWidget(browse)
        form.addLayout(folder_row)
        create_row = QHBoxLayout()
        create = QPushButton(tr('Zapisz 7z do folderu'))
        create.setObjectName('PrimaryButton')
        create.clicked.connect(self.create)
        create_row.addWidget(create)
        upload = QPushButton(tr('Zapisz 7z na Dysku Google'))
        upload.clicked.connect(self.create_drive)
        create_row.addWidget(upload)
        create_row.addStretch(1)
        form.addLayout(create_row)
        hint = QLabel(tr('Kompresja bezstratna LZMA2. Po spakowaniu program sprawdza sumy kontrolne wszystkich plików.'))
        hint.setWordWrap(True)
        form.addWidget(hint)
        layout.addWidget(group)
        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.TextFormat.PlainText)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.result)
        tabs = QTabWidget()
        local = QWidget()
        local_layout = QVBoxLayout(local)
        self.list = QListWidget()
        self.list.setAccessibleName(tr('Kopie lokalne'))
        local_layout.addWidget(self.list)
        self.pending = QLabel()
        self.pending.setWordWrap(True)
        row = QHBoxLayout()
        for label, callback in (
            ('Przywróć wybraną…', self.restore_selected),
            ('Wybierz plik kopii…', self.restore_file), ('Wybierz rozpakowany folder…', self.restore_directory),
        ):
            button = QPushButton(tr(label))
            button.clicked.connect(callback)
            row.addWidget(button)
        local_layout.addLayout(row)
        row = QHBoxLayout()
        for label, callback in (
            ('Anuluj przywracanie', self.cancel_restore),
            ('Otwórz folder kopii', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder.text()))),
        ):
            button = QPushButton(tr(label))
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch(1)
        local_layout.addLayout(row)
        retention = QLabel(tr('Automatyczne kopie: 7 dziennych i 10 sprzed importu. Kopie ręczne są zachowywane.'))
        retention.setWordWrap(True)
        local_layout.addWidget(retention)
        tabs.addTab(local, tr('Kopie lokalne'))
        cloud = QWidget()
        cloud_layout = QVBoxLayout(cloud)
        cloud_hint = QLabel(tr('Korzysta ze sparowanego konta Google. Kopie trafiają do folderu „Spedi — backupy”. Na innym komputerze połącz to samo konto.'))
        cloud_hint.setWordWrap(True)
        cloud_layout.addWidget(cloud_hint)
        self.drive_status = QLabel()
        self.drive_status.setWordWrap(True)
        cloud_layout.addWidget(self.drive_status)
        self.drive_list = QListWidget()
        self.drive_list.setAccessibleName(tr('Kopie na Dysku Google'))
        cloud_layout.addWidget(self.drive_list)
        cloud_row = QHBoxLayout()
        for label, callback in (
            ('Połącz / ponów logowanie', self.connect_drive),
            ('Odśwież kopie Google', self.refresh_drive),
            ('Pobierz i przywróć…', self.restore_drive),
        ):
            button = QPushButton(tr(label))
            button.clicked.connect(callback)
            cloud_row.addWidget(button)
        cloud_layout.addLayout(cloud_row)
        tabs.addTab(cloud, 'Google Drive')
        layout.addWidget(tabs, 1)
        layout.addWidget(self.pending)
        restart = QLabel(tr('Przywrócenie zastępuje bazy przy następnym uruchomieniu. Obecne dane otrzymają kopię bezpieczeństwa. Nie trzeba rozpakowywać pliku 7z.'))
        restart.setWordWrap(True)
        layout.addWidget(restart)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        names = {'manual': 'Ręczna', 'daily': 'Dzienna', 'before_import': 'Przed importem',
                 'before_update': 'Przed aktualizacją', 'before_restore': 'Przed przywróceniem'}
        backups = {item['path'].resolve(): item for item in self.manager.list_backups()}
        folder = Path(self.folder.text().strip())
        if folder.is_dir() and folder.resolve() != self.manager.directory:
            backups.update({item['path'].resolve(): item for item in self.manager.list_backups(folder)})
        for backup in sorted(backups.values(), key=lambda item: item['created_at'], reverse=True):
            kind = tr('Bazy i dokumenty') if backup['documents'] else tr('Bazy')
            item = QListWidgetItem(f"{_stamp(backup['created_at'])} · {tr(names[backup['reason']])} · {kind} · {backup['bytes']/1024**2:.1f} MiB")
            item.setData(Qt.ItemDataRole.UserRole, backup['path'])
            item.setToolTip(str(backup['path']))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.pending.setText(tr('Zaplanowano przywrócenie przy następnym uruchomieniu Spedi.')
                             if self.manager.pending.exists() else tr('Nie zaplanowano przywracania.'))

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr('Folder zapisu kopii'), self.folder.text())
        if folder:
            self.folder.setText(folder)
            QSettings('Spedi', 'Backup').setValue('folder', folder)
            self.refresh()

    def _create(self):
        folder = self.folder.text().strip()
        if not folder:
            raise ValueError(tr('Wybierz folder zapisu kopii.'))
        destination = Path(folder)/('spedi-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]+'.7z')
        return run_background(self, tr('Tworzenie i sprawdzanie pełnej kopii 7z…'),
            lambda task: self.manager.export_all(destination, cancelled=task.isInterruptionRequested), cancellable=True)

    def create(self):
        try:
            path = self._create()
            self.result.setText(tr('Zapisano i sprawdzono kopię: ')+str(path))
            self.refresh()
        except InterruptedError:
            pass
        except Exception as exc:
            QMessageBox.warning(self, tr('Kopie danych'), str(exc))

    def create_drive(self):
        from spedi.backup_drive import BackupDrive
        try:
            # Validate pairing before starting a potentially long compression.
            drive = BackupDrive()
            run_background(self, tr('Sprawdzanie połączenia Google…'), lambda task: drive.check_access())
            path = self._create()
            self.result.setText(tr('Zapisano lokalnie: ')+str(path))
            run_background(self, tr('Wysyłanie i sprawdzanie kopii na Dysku Google…'),
                lambda task: drive.upload(path, cancelled=task.isInterruptionRequested), cancellable=True)
            self.result.setText(tr('Kopia lokalna i kopia na Dysku Google zostały sprawdzone: ')+str(path))
            self.refresh()
            self.refresh_drive()
        except InterruptedError:
            pass
        except Exception as exc:
            self._drive_error(exc)

    def _drive_error(self, error):
        self.drive_status.setText(tr('Operacja na Dysku Google nie powiodła się.'))
        if not getattr(error, 'help_url', None):
            QMessageBox.warning(self, 'Google Drive', str(error))
            return
        box = QMessageBox(QMessageBox.Icon.Warning, 'Google Drive', str(error),
                          QMessageBox.StandardButton.Close, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        settings = box.addButton(tr('Włącz Google Drive API…'), QMessageBox.ButtonRole.ActionRole)
        box.exec()
        if box.clickedButton() is settings:
            QDesktopServices.openUrl(QUrl(error.help_url))

    def connect_drive(self):
        from markus_dispatcher.core import google_connection
        try:
            if not google_connection.settings().get('client'):
                QMessageBox.information(self, 'Google Drive', tr('Najpierw skonfiguruj połączenie Google w programie: Dispatcher → Rozliczenie frachtu → Konfiguracja Google.'))
                return
            run_background(self, tr('Zaloguj się do Google w przeglądarce (do 10 minut). Pozostaw Spedi otwarte…'),
                lambda task: google_connection.connect(cancelled=task.isInterruptionRequested, timeout_seconds=600), cancellable=True)
            self.refresh_drive()
        except InterruptedError:
            self.drive_status.setText(tr('Anulowano logowanie Google.'))
        except Exception as exc:
            self._drive_error(exc)

    def refresh_drive(self):
        from spedi.backup_drive import BackupDrive
        try:
            items = run_background(self, tr('Pobieranie listy kopii Google…'),
                lambda task: BackupDrive().list_backups(cancelled=task.isInterruptionRequested), cancellable=True)
            self.drive_status.setText(tr('Dostęp do Dysku Google został potwierdzony.'))
            self.drive_list.clear()
            for data in items:
                item = QListWidgetItem(f"{data['name']} · {int(data.get('size', 0))/1024**2:.1f} MiB")
                item.setData(Qt.ItemDataRole.UserRole, data)
                self.drive_list.addItem(item)
            if items:
                self.drive_list.setCurrentRow(0)
            else:
                self.drive_status.setText(tr('Dostęp do Dysku Google został potwierdzony.')+'\n'+tr('Na tym koncie nie ma jeszcze kopii programu.'))
        except InterruptedError:
            pass
        except Exception as exc:
            self._drive_error(exc)

    def restore_drive(self):
        from spedi.backup_drive import BackupDrive
        if not (item := self.drive_list.currentItem()):
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            def download(task):
                path = BackupDrive().download(data, self.manager.directory, cancelled=task.isInterruptionRequested)
                try:
                    self.manager.inspect(path, verify=True, cancelled=task.isInterruptionRequested)
                    return path
                except BaseException:
                    path.unlink(missing_ok=True)
                    raise
            path = run_background(self, tr('Pobieranie i sprawdzanie kopii Google…'), download, cancellable=True)
            self.refresh()
            self._schedule(path)
        except InterruptedError:
            pass
        except Exception as exc:
            self._drive_error(exc)

    def restore_selected(self):
        if item := self.list.currentItem():
            self._schedule(item.data(Qt.ItemDataRole.UserRole))

    def restore_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('Wybierz kopię danych'), self.folder.text(), 'Backup (*.7z *.zip)')
        if path:
            self._schedule(path)

    def restore_directory(self):
        path = QFileDialog.getExistingDirectory(self, tr('Wybierz folder zawierający manifest.json'), self.folder.text())
        if path:
            self._schedule(path)

    def _schedule(self, path):
        if QMessageBox.question(self, tr('Przywracanie danych'), tr(
            'Przywrócić tę kopię przy następnym uruchomieniu? Bieżące dane otrzymają wcześniej osobną kopię. '
            'Zamknij pozostałe kopie Spedi przed ponownym uruchomieniem.'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            run_background(self, tr('Sprawdzanie kopii…'), lambda task: self.manager.schedule_restore(path))
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, tr('Przywracanie danych'), str(exc))

    def cancel_restore(self):
        self.manager.pending.unlink(missing_ok=True)
        self.refresh()


class ProfileHistoryDialog(QDialog):
    def __init__(self, revisions, restore, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Historia korekt profilu'))
        self.resize(760, 440)
        self.restore = restore
        layout = QVBoxLayout(self)
        description = QLabel(tr('Wybierz zmianę, aby wrócić do stanu sprzed niej. Zachowywane jest 25 ostatnich zmian. Przywrócenie również można cofnąć.'))
        description.setWordWrap(True)
        layout.addWidget(description)
        self.list = QListWidget()
        for revision in revisions:
            summary = revision['summary']
            kind = tr('Przywrócenie') if revision['reason'] == 'restore' else tr('Korekta')
            detail = (f"+{summary['added']} / −{summary['removed']} / Δ{summary['changed']}")
            if summary['axis_changed']:
                detail += ' · '+tr('zmiana początku cyklu')
            if summary['title_changed']:
                detail += ' · '+tr('zmiana nazwy')
            item = QListWidgetItem(f"{_stamp(revision['created_at'])} · {kind} · {detail}")
            item.setData(Qt.ItemDataRole.UserRole, revision['id'])
            self.list.addItem(item)
        layout.addWidget(self.list)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            layout.addWidget(QLabel(tr('Brak zapisanej historii korekt tego profilu.')))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        apply = buttons.addButton(tr('Przywróć stan sprzed zmiany'), QDialogButtonBox.ButtonRole.ActionRole)
        apply.setEnabled(bool(revisions))
        apply.clicked.connect(self._restore)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _restore(self):
        if item := self.list.currentItem():
            if QMessageBox.question(self, tr('Historia korekt profilu'), tr('Przywrócić wskazany stan profilu?'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                try:
                    self.restore(item.data(Qt.ItemDataRole.UserRole))
                    self.accept()
                except Exception as exc:
                    QMessageBox.warning(self, tr('Historia korekt profilu'), str(exc))
