from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QVBoxLayout,
)

from spedi import __version__
from spedi.background import run_background
from spedi.i18n import tr


class UpdateDialog(QDialog):
    def __init__(self, info, service, backups, parent=None):
        super().__init__(parent)
        self.info, self.service, self.backups = info.validate(), service, backups
        self.setWindowTitle(tr('Aktualizacje Spedi'))
        self.resize(760, 530)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('Uruchomiona wersja: ')+__version__))
        layout.addWidget(QLabel(tr('Opublikowana wersja: ')+info.version))
        if info.newer_than():
            status = tr('Dostępna jest nowsza wersja. Zapisz pracę i zamknij Spedi przed instalacją.')
        elif info.version == __version__:
            status = tr('Używasz aktualnego wydania Spedi.')
        else:
            status = tr('Uruchomiona wersja jest nowsza od ostatniego opublikowanego wydania.')
        label = QLabel(status)
        label.setWordWrap(True)
        layout.addWidget(label)
        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(info.notes)
        layout.addWidget(notes)
        self.automatic = QCheckBox(tr('Sprawdzaj nowe wydania w tle raz dziennie'))
        self.automatic.setChecked(service.automatic)
        self.automatic.toggled.connect(self._set_automatic)
        layout.addWidget(self.automatic)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        download = buttons.addButton(tr('Kopia baz i pobieranie instalatora'), QDialogButtonBox.ButtonRole.ActionRole)
        download.setEnabled(info.newer_than())
        download.clicked.connect(self._download)
        release = buttons.addButton(tr('Zobacz wydanie'), QDialogButtonBox.ButtonRole.ActionRole)
        release.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(info.release_url)))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_automatic(self, enabled):
        try:
            self.service.set_automatic(enabled)
        except OSError as exc:
            self.automatic.blockSignals(True)
            self.automatic.setChecked(not enabled)
            self.automatic.blockSignals(False)
            QMessageBox.warning(self, tr('Aktualizacje Spedi'), str(exc))

    def _download(self):
        try:
            run_background(self, tr('Kopia baz przed aktualizacją…'),
                           lambda task: self.backups.create('before_update', documents=False))
            QDesktopServices.openUrl(QUrl(self.info.installer_url))
        except Exception as exc:
            QMessageBox.warning(self, tr('Nie przygotowano aktualizacji'), str(exc))
