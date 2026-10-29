"""Run an explicit operation off the UI thread while keeping its dialog alive.

The operation owns its database connections. Cancellation is cooperative: a
write finishes its current transaction before the dialog can close.
"""
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout

from spedi.i18n import tr as _tr


class BackgroundTask(QThread):
    progress = Signal(int, int)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation(self)
        except Exception as error:
            self.error = error
        finally:
            self.operation = None


class OperationDialog(QDialog):
    def __init__(self, parent, label, operation, cancellable=False):
        super().__init__(parent)
        self.setWindowTitle(_tr('Spedi'))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        self.label = QLabel(label)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.cancel_button = QPushButton(_tr('Anuluj'))
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.cancellable = cancellable
        self.task = BackgroundTask(operation, self)
        self.task.progress.connect(self._progress)
        self.task.finished.connect(self.accept)

    def _progress(self, completed, total):
        self.bar.setRange(0, total)
        self.bar.setValue(completed)

    def reject(self):
        if self.task.isRunning():
            if self.cancellable:
                self.task.requestInterruption()
                self.cancel_button.setEnabled(False)
                self.label.setText(_tr('Kończenie bieżącej operacji…'))
            return
        super().reject()

    def closeEvent(self, event):
        if self.task.isRunning():
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


def run_background(parent, label, operation, *, cancellable=False):
    dialog = OperationDialog(parent, label, operation, cancellable)
    try:
        dialog.task.start()
        dialog.exec()
        dialog.task.wait()
        if dialog.task.error is not None:
            raise dialog.task.error
        return dialog.task.result
    finally:
        dialog.deleteLater()
