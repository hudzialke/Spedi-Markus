from PySide6.QtCore import QThread, Signal

from spedi.mobile_sync import sync_now


class MobileSyncWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.completed.emit(sync_now(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))
