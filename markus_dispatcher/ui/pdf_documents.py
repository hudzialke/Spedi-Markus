"""Exclusive leases for a small pool of closed Qt PDF backends.

Repeated backend creation grew native allocations in the local Qt 6.11.2
probe; reloading one backend kept memory flat. Documents are never used by two
tasks at once. Thread transfers follow QObject.moveToThread's owner rule.
"""
from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice, QThread
from PySide6.QtPdf import QPdfDocument
from markus_dispatcher.core.document_import import MAX_FILE_BYTES


class PdfDocumentLease:
    def __init__(self):
        self.app = QCoreApplication.instance()
        if self.app is None or QThread.currentThread() != self.app.thread():
            raise RuntimeError('PDF backends must be leased from the application thread')
        self.pool = getattr(self.app, '_spedi_pdf_documents', None)
        if self.pool is None:
            self.pool = []
            self.app._spedi_pdf_documents = self.pool
        self.document = self.pool.pop() if self.pool else QPdfDocument(self.app)
        if not hasattr(self.document, '_spedi_buffer'):
            self.document._spedi_buffer = QBuffer(self.document)
        self.buffer = self.document._spedi_buffer
        self.released = False

    def load_path(self, path):
        # Qt retains its internal QFile even after close(). A bounded buffer
        # releases the OS handle immediately and follows the document's thread.
        self._close()
        with open(path, 'rb') as source:
            data = source.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError('Plik jest większy niż 50 MB. Podziel go lokalnie na mniejsze części.')
        self.buffer.setData(data)
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.document.load(self.buffer)
        return self.document.error()

    def _close(self):
        self.document.close()
        self.buffer.close()
        self.buffer.setData(b'')

    def move_to(self, thread):
        self.document.setParent(None)
        if not self.document.moveToThread(thread):
            raise RuntimeError('Unable to assign the PDF backend to its worker')

    def park(self):
        """Called by the current owner, before a worker thread exits."""
        self._close()
        if self.document.thread() != self.app.thread():
            if not self.document.moveToThread(self.app.thread()):
                raise RuntimeError('Unable to return the PDF backend to the application thread')

    def release(self, *_):
        if self.released:
            return
        self.released = True
        try:
            self._close()
            self.document.setParent(self.app)
            if len(self.pool) < 4:
                self.pool.append(self.document)
            else:
                self.document.deleteLater()
        except RuntimeError:
            # QApplication may already have destroyed its children at shutdown.
            pass
