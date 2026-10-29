"""Small data-management dialogs using the application's existing Qt controls."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from spedi.background import run_background
from spedi.i18n import tr


def _stamp(value):
    return datetime.fromisoformat(value).astimezone().strftime('%d.%m.%Y %H:%M:%S')


class BackupDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle(tr('Kopie danych'))
        self.resize(900, 510)
        layout = QVBoxLayout(self)
        description = QLabel(tr('Kopie obejmują bazy, archiwa DDD i dokumenty. Dane logowania do usług pozostają poza kopiami.'))
        description.setWordWrap(True)
        layout.addWidget(description)
        self.list = QListWidget()
        layout.addWidget(self.list)
        self.pending = QLabel()
        self.pending.setWordWrap(True)
        layout.addWidget(self.pending)
        row = QHBoxLayout()
        for label, callback in (
            ('Utwórz kopię', self.create), ('Przywróć wybraną…', self.restore_selected),
            ('Wybierz plik kopii…', self.restore_file), ('Anuluj przywracanie', self.cancel_restore),
            ('Otwórz folder', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(manager.directory)))),
        ):
            button = QPushButton(tr(label))
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        retention = QLabel(tr('Automatyczne kopie: 7 dziennych i 10 sprzed importu. Kopie ręczne są zachowywane.'))
        retention.setWordWrap(True)
        layout.addWidget(retention)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.list.clear()
        names = {'manual': 'Ręczna', 'daily': 'Dzienna', 'before_import': 'Przed importem',
                 'before_update': 'Przed aktualizacją', 'before_restore': 'Przed przywróceniem'}
        for backup in self.manager.list_backups():
            kind = tr('Bazy i dokumenty') if backup['documents'] else tr('Bazy')
            item = QListWidgetItem(f"{_stamp(backup['created_at'])} · {tr(names[backup['reason']])} · {kind} · {backup['bytes']/1024**2:.1f} MiB")
            item.setData(Qt.ItemDataRole.UserRole, backup['path'])
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.pending.setText(tr('Zaplanowano przywrócenie przy następnym uruchomieniu Spedi.')
                             if self.manager.pending.exists() else tr('Nie zaplanowano przywracania.'))

    def create(self):
        try:
            run_background(self, tr('Tworzenie kopii danych…'),
                           lambda task: self.manager.create(cancelled=task.isInterruptionRequested), cancellable=True)
            self.refresh()
        except InterruptedError:
            pass
        except Exception as exc:
            QMessageBox.warning(self, tr('Kopie danych'), str(exc))

    def restore_selected(self):
        if item := self.list.currentItem():
            self._schedule(item.data(Qt.ItemDataRole.UserRole))

    def restore_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('Wybierz kopię danych'), '', 'ZIP (*.zip)')
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
