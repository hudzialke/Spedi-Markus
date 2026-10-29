from __future__ import annotations

from spedi.i18n import tr as _tr

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from markus_dispatcher import config
from markus_dispatcher.integrations import firebase_sync


class _SyncWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            result = firebase_sync.sync_now()
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class FirebaseSyncDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Sync Firebase"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)

        intro = QLabel(
            f"{_tr('PC biuro ⇄ Firebase ⇄ telefon.\nKlucz service account: ')}{config.FIREBASE_SERVICE_ACCOUNT}"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.uid_edit = QLineEdit()
        setup = firebase_sync.load_setup()
        self.uid_edit.setText(setup.uid)
        self.uid_edit.setPlaceholderText(_tr("User UID z Authentication"))
        form.addRow("Firebase UID", self.uid_edit)
        layout.addLayout(form)

        row = QHBoxLayout()
        open_btn = QPushButton(_tr("Otwórz folder credentials"))
        open_btn.clicked.connect(self._open_folder)
        save_btn = QPushButton(_tr("Zapisz UID"))
        save_btn.clicked.connect(self._save_uid)
        row.addWidget(open_btn)
        row.addWidget(save_btn)
        layout.addLayout(row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        if setup.configured:
            self.log.setPlainText(_tr("Konfiguracja OK — możesz synchronizować."))
        else:
            self.log.setPlainText(setup.missing_reason)
        layout.addWidget(self.log)

        self.sync_btn = QPushButton(_tr("Synchronizuj teraz"))
        self.sync_btn.setObjectName("PrimaryButton")
        self.sync_btn.clicked.connect(self._run_sync)
        layout.addWidget(self.sync_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close is not None:
            close.clicked.connect(self.reject)
        layout.addWidget(buttons)

        self._worker: _SyncWorker | None = None

    def _open_folder(self) -> None:
        path = firebase_sync.ensure_firebase_dir()
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _save_uid(self) -> None:
        firebase_sync.save_uid(self.uid_edit.text())
        setup = firebase_sync.load_setup()
        self.log.setPlainText(
            _tr("UID zapisany.")
            if setup.uid
            else _tr("UID pusty — wklej User UID z Firebase Authentication.")
        )
        if setup.configured:
            self.log.append(_tr("Konfiguracja kompletna."))
        elif setup.missing_reason:
            self.log.append(setup.missing_reason)

    def _run_sync(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        firebase_sync.save_uid(self.uid_edit.text())
        setup = firebase_sync.load_setup()
        if not setup.configured:
            QMessageBox.warning(self, _tr("Sync Firebase"), setup.missing_reason)
            return
        self.sync_btn.setEnabled(False)
        self.log.setPlainText(_tr("Synchronizacja…"))
        self._worker = _SyncWorker(self)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.finished.connect(self._sync_finished)
        self._worker.start()

    def _sync_finished(self):
        worker, self._worker = self._worker, None
        worker.deleteLater()

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            self.log.append(_tr('Poczekaj na zakończenie synchronizacji.'))
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            self.log.append(_tr('Poczekaj na zakończenie synchronizacji.'))
        else:
            super().closeEvent(event)

    def _on_done(self, result: object) -> None:
        self.sync_btn.setEnabled(True)
        from markus_dispatcher.integrations.firebase_sync import SyncResult

        assert isinstance(result, SyncResult)
        self.log.setPlainText(result.message)
        if not result.ok:
            QMessageBox.warning(self, _tr("Sync Firebase"), result.message)

    def _on_fail(self, message: str) -> None:
        self.sync_btn.setEnabled(True)
        self.log.setPlainText(message)
        QMessageBox.warning(self, _tr("Sync Firebase"), message)
