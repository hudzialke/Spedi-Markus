"""Local document mapping dialog. Only selected fields leave this dialog, on Apply."""
from __future__ import annotations

from pathlib import Path
import tempfile
from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QIODevice, QRectF, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from markus_dispatcher.core import document_import as readers
from markus_dispatcher.ui.pdf_documents import PdfDocumentLease
from spedi.i18n import tr as _tr


FIELDS = (
    ('ref_numbers', 'Numer / referencje zlecenia'),
    ('client_name', 'Zleceniodawca'),
    ('driver_note', 'Kierowca / oznaczenie miesięczne'),
    ('load_address', 'Adres załadunku'),
    ('load_datetime', 'Termin załadunku'),
    ('unload_address', 'Adres rozładunku'),
    ('unload_datetime', 'Termin rozładunku'),
    ('pallets', 'Towar / palety'),
    ('price', 'Stawka frachtu (EUR)'),
    ('notes', 'Uwagi do transportu'),
    ('pod_requirements', 'Wymagania POD — pełna treść'),
)
REQUIRED = ('ref_numbers', 'client_name', 'load_address', 'load_datetime', 'unload_address', 'unload_datetime', 'pod_requirements')


@dataclass(frozen=True)
class PdfTextIndex:
    characters: tuple
    whitespace: tuple[int, ...]


def index_pdf_page(document: QPdfDocument, page: int, cancelled=lambda: False) -> PdfTextIndex | None:
    """Prepare page geometry once. Contains value objects, never a shared PDF QObject."""
    all_text = document.getAllText(page)
    count = all_text.endIndex()
    if count > 30_000:
        raise ValueError(_tr('Strona ma zbyt wiele znaków. Użyj lokalnego OCR zaznaczenia lub wpisz treść ręcznie.'))
    chars = []
    whitespace = [0]
    for index in range(max(0, count)):
        if index % 16 == 0 and cancelled():
            return None
        item = document.getSelectionAtIndex(page, index, 1)
        box = item.boundingRectangle()
        text = item.text()
        whitespace.append(whitespace[-1] + int(any(c.isspace() for c in text)))
        if text and not box.isEmpty() and not text.isspace():
            chars.append((box, text, index))
    return PdfTextIndex(tuple(chars), tuple(whitespace))


def pdf_text_in_rect(document: QPdfDocument | None, page: int, area: QRectF,
                     prepared: PdfTextIndex | None = None) -> str:
    """Read ONLY characters inside the crop; never follow whole-page text flow."""
    prepared = prepared if prepared is not None else index_pdf_page(document, page)
    chars = [item for item in prepared.characters if area.contains(item[0].center())]
    chars.sort(key=lambda item: (item[0].center().y(), item[0].left()))
    lines = []
    for box, text, index in chars:
        line = next((line for line in reversed(lines[-3:])
                     if (min(line[0][0].bottom(), box.bottom()) - max(line[0][0].top(), box.top()) > 0
                         or abs(line[0][0].bottom() - box.bottom()) <= max(2, line[0][0].height()*.35))), None)
        if line is None:
            lines.append([(box, text, index)])
        else:
            line.append((box, text, index))
    output = []
    for line in lines:
        line.sort(key=lambda item: item[0].left())
        result, previous, previous_index = [], None, None
        for box, text, index in line:
            if previous is not None and index != previous_index + 1:
                # Glyph bounding boxes describe ink, not advances. Adding spaces
                # from small geometric gaps breaks words such as UNLOAD or 12.
                if index > previous_index + 1:
                    if prepared.whitespace[index] > prepared.whitespace[previous_index+1]:
                        result.append(' ')
                elif box.left() - previous.right() > box.height():
                    result.append(' ')
            result.append(text)
            previous = box
            previous_index = index
        output.append(''.join(result))
    return '\n'.join(output).strip()


class PdfPageTask(QThread):
    rendered = Signal(int, object)
    indexed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, path, page, generation, stamp, parent=None):
        super().__init__(parent)
        self.path, self.page, self.generation, self.stamp = path, page, generation, stamp
        self._document_lease = PdfDocumentLease()
        self.finished.connect(self._return_document, Qt.ConnectionType.QueuedConnection)
        self.destroyed.connect(self._document_lease.release)

    def start(self, priority=QThread.Priority.InheritPriority):
        self._document_lease.move_to(self)
        super().start(priority)

    @Slot()
    def _return_document(self):
        self._document_lease.release()

    def run(self):
        document = self._document_lease.document
        try:
            def unchanged():
                stat = self.path.stat()
                return (stat.st_mtime_ns, stat.st_size) == self.stamp
            if not unchanged() or self._document_lease.load_path(self.path) != QPdfDocument.Error.None_:
                raise ValueError(_tr('Dokument zmienił się lub jest niedostępny. Otwórz go ponownie.'))
            size = document.pagePointSize(self.page)
            factor = min(1.6, 1800/max(size.width(), size.height()))
            image = document.render(self.page, QSize(round(size.width()*factor), round(size.height()*factor)))
            if self.isInterruptionRequested():
                return
            if image.isNull() or not unchanged():
                raise ValueError(_tr('Nie udało się przygotować strony. Otwórz dokument ponownie.'))
            self.rendered.emit(self.generation, image)
            prepared = index_pdf_page(document, self.page, self.isInterruptionRequested)
            if prepared is not None and not self.isInterruptionRequested():
                if not unchanged():
                    raise ValueError(_tr('Dokument zmienił się. Otwórz go ponownie.'))
                self.indexed.emit(self.generation, prepared)
        except ValueError as error:
            self.failed.emit(self.generation, str(error))
        except Exception:
            self.failed.emit(self.generation, _tr('Nie można przygotować strony. Użyj OCR lub otwórz dokument ponownie.'))
        finally:
            self._document_lease.park()


class LocalTextPreview(QTextBrowser):
    """Never fetch linked images/resources, nor follow document hyperlinks."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setStyleSheet('QTextBrowser { background: white; color: #101820; padding: 18px; }')

    def loadResource(self, kind, name):
        return None


class DocumentCanvas(QWidget):
    region_selected = Signal(QRectF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.zoom = 1.0
        self.area = QRectF()
        self.start = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_image(self, image: QImage, zoom: float = 1.0):
        self.image = image
        self.area = QRectF()
        self.zoom = zoom
        self.setFixedSize(max(1, round(image.width()*zoom)), max(1, round(image.height()*zoom)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        if not self.image.isNull():
            painter.drawImage(QRectF(self.rect()), self.image)
        if not self.area.isEmpty():
            painter.fillRect(self.area, QColor(11, 61, 74, 32))
            painter.setPen(QPen(QColor('#0b3d4a'), 2))
            painter.drawRect(self.area)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.image.isNull():
            self.start = event.position()
            self.area = QRectF(self.start, self.start)
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.area = QRectF(self.start, event.position()).normalized().intersected(QRectF(self.rect()))
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self.area = QRectF(self.start, event.position()).normalized().intersected(QRectF(self.rect()))
        self.start = None
        self.update()
        if self.area.width() > 3 and self.area.height() > 3:
            self.region_selected.emit(QRectF(self.area.x()/self.width(), self.area.y()/self.height(),
                                            self.area.width()/self.width(), self.area.height()/self.height()))


class OcrTask(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, image: QImage, parent=None):
        super().__init__(parent)
        self.image = image.scaled(2600, 2600, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation) if max(image.width(), image.height()) > 2600 else image.copy()

    def run(self):
        try:
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            self.image.save(buffer, 'PNG')
            self.succeeded.emit(readers.run_local_ocr(bytes(buffer.data())))
        except ValueError as error:
            self.failed.emit(str(error))
        except Exception:
            self.failed.emit(_tr('Nie udało się wykonać lokalnego OCR. Wpisz treść ręcznie.'))


class ReadTask(QThread):
    succeeded = Signal(object, str)
    failed = Signal(str)

    def __init__(self, path: Path, destination: Path, parent=None):
        super().__init__(parent)
        self.path, self.destination = path, destination

    def run(self):
        try:
            if self.path.suffix.lower() == '.doc':
                self.succeeded.emit([], str(readers.convert_doc(self.path, self.destination)))
            elif self.path.suffix.lower() == '.docx':
                self.succeeded.emit(readers.read_docx(self.path), '')
            else:
                self.succeeded.emit(readers.read_spreadsheet(self.path), '')
        except ValueError as error:
            self.failed.emit(str(error))
        except Exception:
            self.failed.emit(_tr('Nie można odczytać dokumentu. Sprawdź format, szyfrowanie i czy plik nie jest uszkodzony.'))


class DocumentImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr('Import z dokumentu — lokalnie'))
        self.resize(1280, 850)
        self.setMinimumSize(980, 720)
        self.values: dict[str, str] = {}
        self.sources: dict[str, dict] = {}
        self.source_path: Path | None = None
        self.clipboard_image = QImage()
        self._temporary = tempfile.TemporaryDirectory(prefix='spedi-import-', ignore_cleanup_errors=True)
        self._pdf_lease = PdfDocumentLease()
        self.pdf = self._pdf_lease.document
        self.destroyed.connect(self._pdf_lease.release)
        self.sections = []
        self.pdf_mode = False
        self.current = 0
        self._selected_source = {}
        self._crop = QImage()
        self._ocr = None
        self._reader = None
        self._has_document = False
        self._applied = False
        self._pdf_worker = None
        self._pdf_generation = 0
        self._pending_pdf = None
        self._page_cache = OrderedDict()
        self._page_index = None
        self._pdf_index_failed = False
        self._pending_region = None
        self._closing = None

        root = QVBoxLayout(self)
        title = QLabel(_tr('Import z dokumentu'))
        title.setObjectName('BrandTitle')
        root.addWidget(title)
        hint = QLabel(_tr('Otwórz dokument lub wklej obraz. Wybierz pole, zaznacz tekst albo obszar i przypisz dane.'))
        hint.setWordWrap(True)
        root.addWidget(hint)
        toolbar = QHBoxLayout()
        self.open_btn = QPushButton(_tr('Otwórz dokument…'))
        self.open_btn.setToolTip(_tr('PDF, Word, Excel, CSV lub obraz'))
        self.open_btn.clicked.connect(self.choose_file)
        self.paste_btn = QPushButton(_tr('Wklej obraz'))
        self.paste_btn.setToolTip(_tr('Ctrl+V w panelu dokumentu'))
        self.paste_btn.clicked.connect(self.paste_image)
        toolbar.addWidget(self.open_btn)
        toolbar.addWidget(self.paste_btn)
        self.page_combo = QComboBox()
        self.page_combo.setMinimumWidth(160)
        self.page_combo.setAccessibleName(_tr('Strona lub arkusz'))
        self.page_combo.currentIndexChanged.connect(self._show_page)
        toolbar.addWidget(self.page_combo, 1)
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItem(_tr('Dopasuj szerokość'), 0)
        for percent in (50, 75, 100, 125, 150, 200):
            self.zoom_combo.addItem(f'{percent}%', percent/100)
        self.zoom_combo.setCurrentIndex(0)
        self.zoom_combo.setAccessibleName(_tr('Powiększenie dokumentu'))
        self.zoom_combo.currentIndexChanged.connect(self._zoom)
        toolbar.addWidget(self.zoom_combo)
        root.addLayout(toolbar)
        self.file_label = QLabel(_tr('Przetwarzanie lokalne. Importowane zlecenie będzie wyłączone z wysyłki i synchronizacji.'))
        self.file_label.setWordWrap(True)
        root.addWidget(self.file_label)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.preview_stack = QStackedWidget()
        self.empty = QLabel(_tr('Wybierz dokument z dysku lub wklej obraz ze schowka.'))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.preview_stack.addWidget(self.empty)
        self.canvas = DocumentCanvas()
        self.canvas.region_selected.connect(self._region)
        self.canvas_scroll = QScrollArea()
        self.canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.canvas_scroll.setWidget(self.canvas)
        self.preview_stack.addWidget(self.canvas_scroll)
        self.text_preview = LocalTextPreview()
        self.text_preview.selectionChanged.connect(self._text_selected)
        self.preview_stack.addWidget(self.text_preview)
        self.splitter.addWidget(self.preview_stack)

        inspector = QWidget()
        inspector.setMinimumWidth(340)
        inspector.setStyleSheet('QPushButton, QPlainTextEdit { font-size: 14px; }')
        right = QVBoxLayout(inspector)
        right.setContentsMargins(12, 0, 0, 0)
        self.progress = QLabel()
        right.addWidget(self.progress)
        self.field_scroll = QScrollArea()
        self.field_scroll.setMinimumHeight(180)
        self.field_scroll.setWidgetResizable(True)
        field_host = QWidget()
        field_layout = QVBoxLayout(field_host)
        field_layout.setContentsMargins(0, 0, 4, 0)
        self.edits, self.field_buttons, self.source_labels = {}, {}, {}
        for index, (key, label) in enumerate(FIELDS):
            button = QPushButton(f'{index+1:02d}  ' + _tr(label))
            button.setCheckable(True)
            button.setObjectName('ChipButton')
            button.clicked.connect(lambda checked=False, i=index: self._activate(i))
            edit = QPlainTextEdit()
            edit.setAccessibleName(_tr(label))
            edit.setPlaceholderText(_tr('Przypisz zaznaczenie lub wpisz ręcznie'))
            edit.setFixedHeight(80 if key in ('pod_requirements', 'load_address', 'unload_address', 'notes') else 56)
            edit.textChanged.connect(lambda k=key: self._edited(k))
            source = QLabel()
            source.setWordWrap(True)
            source.setStyleSheet('font-size: 12px; color: #5a6672;')
            field_layout.addWidget(button)
            field_layout.addWidget(edit)
            field_layout.addWidget(source)
            self.edits[key], self.field_buttons[key], self.source_labels[key] = edit, button, source
        field_layout.addStretch()
        self.field_scroll.setWidget(field_host)
        right.addWidget(self.field_scroll, 1)
        self.target = QLabel()
        self.target.setWordWrap(True)
        right.addWidget(self.target)
        self.selection = QPlainTextEdit()
        self.selection.setAccessibleName(_tr('Treść zaznaczenia do przypisania'))
        self.selection.setPlaceholderText(_tr('Treść zaznaczenia. Możesz ją poprawić przed przypisaniem.'))
        self.selection.setFixedHeight(90)
        self.selection.textChanged.connect(self._selection_edited)
        right.addWidget(self.selection)
        assignment = QHBoxLayout()
        self.assign_btn = QPushButton(_tr('Przypisz i dalej'))
        self.assign_btn.setObjectName('PrimaryButton')
        self.assign_btn.clicked.connect(self.assign_selection)
        assignment.addWidget(self.assign_btn)
        skip = QPushButton(_tr('Pomiń'))
        skip.clicked.connect(lambda: self._activate(min(self.current+1, len(FIELDS)-1)))
        assignment.addWidget(skip)
        right.addLayout(assignment)
        self.ocr_btn = QPushButton(_tr('Odczytaj obszar (OCR lokalny)'))
        self.ocr_btn.clicked.connect(self._run_ocr)
        self.ocr_btn.setEnabled(False)
        right.addWidget(self.ocr_btn)
        self.status = QLabel(_tr('Dla PDF zaznacz prostokąt wokół jednego bloku. Dla Worda i Excela zaznacz tekst.'))
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(44)
        right.addWidget(self.status)
        self.splitter.addWidget(inspector)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([750, 480])
        self.splitter.splitterMoved.connect(lambda *args: self._zoom() if not self.zoom_combo.currentData() else None)
        root.addWidget(self.splitter, 1)

        bottom = QHBoxLayout()
        self.warning = QLabel(_tr('Dane trafią do edytora po sprawdzeniu. Zapis zlecenia wykonujesz osobno.'))
        self.warning.setWordWrap(True)
        bottom.addWidget(self.warning, 1)
        cancel = QPushButton(_tr('Anuluj'))
        cancel.clicked.connect(self.reject)
        self.apply_btn = QPushButton(_tr('Przenieś do zlecenia'))
        self.apply_btn.setObjectName('PrimaryButton')
        self.apply_btn.clicked.connect(self._apply)
        bottom.addWidget(cancel)
        bottom.addWidget(self.apply_btn)
        root.addLayout(bottom)
        # Only Ctrl+V on the document panel pastes an image. Editors keep normal paste.
        self.paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self.preview_stack)
        self.paste_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.paste_shortcut.activated.connect(self.paste_image)
        self._activate(0)
        self._selection_edited()
        self._update_progress()

    def _busy(self):
        return any(worker and worker.isRunning() for worker in (self._ocr, self._reader))

    def _set_busy(self, busy):
        for widget in (self.open_btn, self.paste_btn, self.page_combo, self.zoom_combo, self.apply_btn, self.assign_btn,
                       self.canvas, self.text_preview, self.selection):
            widget.setEnabled(not busy)
        self.ocr_btn.setEnabled(not busy and not self._crop.isNull())
        if not busy:
            self._selection_edited()
            self._update_progress()

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, _tr('Wybierz dokument'), '', _tr(readers.FILE_FILTER))
        if path:
            self.open_path(Path(path))

    def _can_replace_document(self):
        if not self._has_document:
            return True
        return QMessageBox.question(self, _tr('Zmień dokument'),
            _tr('Zmiana dokumentu wyczyści wskazane dane tej próby. Kontynuować?'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _clear_document(self):
        if self._pdf_lease.released:
            self._pdf_lease = PdfDocumentLease()
            self.pdf = self._pdf_lease.document
            self.destroyed.connect(self._pdf_lease.release)
        self._cancel_pdf()
        self._page_cache.clear()
        self._page_index = None
        self._pdf_lease._close()
        self.sections = []
        self.source_path = None
        self.clipboard_image = QImage()
        self.pdf_mode = False
        self._has_document = False
        self._crop = QImage()
        self.values.clear()
        self.sources.clear()
        self._selected_source = {}
        for edit in self.edits.values():
            edit.clear()
        self.selection.clear()
        self.text_preview.clear()
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        self.page_combo.blockSignals(False)
        self.preview_stack.setCurrentWidget(self.empty)
        self.ocr_btn.setEnabled(False)
        self._activate(0)

    def open_path(self, path: Path):
        if self._busy() or not self._can_replace_document():
            return
        try:
            readers.check_file(path)
            suffix = path.suffix.lower()
            if suffix not in {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.xlsm', '.csv', '.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}:
                raise ValueError(_tr('Nieobsługiwany format dokumentu.'))
            self._clear_document()
            self.source_path = path.resolve()
            if suffix == '.pdf':
                self._load_pdf(path)
            elif suffix in ('.doc', '.docx', '.xls', '.xlsx', '.xlsm', '.csv'):
                self._reader = ReadTask(path, Path(self._temporary.name), self)
                self._reader.succeeded.connect(self._read_ready)
                self._reader.failed.connect(self._read_failed)
                self._reader.finished.connect(self._background_finished)
                self._set_busy(True)
                self.status.setText(_tr('Przygotowuję lokalny podgląd dokumentu…'))
                self._reader.start()
            else:
                from PySide6.QtGui import QImageReader
                reader = QImageReader(str(path))
                reader.setAutoTransform(True)
                size = reader.size()
                if not size.isValid() or size.width()*size.height() > 40_000_000:
                    raise ValueError(_tr('Obraz jest nieprawidłowy lub przekracza limit 40 megapikseli.'))
                image = reader.read()
                if image.isNull():
                    raise ValueError(_tr('Nie można odczytać obrazu.'))
                self._load_image(image)
            self.file_label.setText(path.name + ' · ' + _tr('tylko lokalnie'))
        except Exception as error:
            self._read_failed(str(error) if isinstance(error, ValueError) else _tr('Nie można otworzyć dokumentu.'))

    def _load_pdf(self, path):
        if self._pdf_lease.load_path(path) != QPdfDocument.Error.None_ or not self.pdf.pageCount():
            raise ValueError(_tr('Nie można otworzyć PDF. Sprawdź, czy plik jest poprawny i nie wymaga hasła.'))
        self.pdf_mode = True
        self._pdf_path = Path(path)
        stat = self._pdf_path.stat()
        self._pdf_stamp = (stat.st_mtime_ns, stat.st_size)
        self._has_document = True
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        for number in range(self.pdf.pageCount()):
            self.page_combo.addItem(_tr('Strona ') + str(number+1), number)
        self.page_combo.blockSignals(False)
        self._show_page(0)

    def _read_ready(self, sections, pdf_path):
        if pdf_path:
            try:
                self._load_pdf(Path(pdf_path))
            except ValueError as error:
                self._read_failed(str(error))
            return
        self.sections = sections
        self._has_document = bool(sections)
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        for section in sections:
            self.page_combo.addItem(section.title)
        self.page_combo.blockSignals(False)
        if sections:
            self._show_page(0)
            self.status.setText(_tr('Podgląd treści zachowuje tabele. Układ wydruku Word/Excel może różnić się od oryginału; formuły Excela pokazują zapisane wyniki.'))
        else:
            self._read_failed(_tr('Dokument nie zawiera treści do zaznaczenia.'))

    def _read_failed(self, message):
        self._has_document = False
        self.status.setText(message)
        self.preview_stack.setCurrentWidget(self.empty)
        self._update_progress()

    def _load_image(self, image):
        self.clipboard_image = image.copy()
        self._has_document = True
        self.page_combo.addItem(_tr('Obraz'))
        self._show_page(0)

    def paste_image(self):
        if self._busy():
            return
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self.status.setText(_tr('W schowku nie ma obrazu. Skopiuj obraz i kliknij „Wklej obraz”.'))
            return
        if image.width()*image.height() > 40_000_000:
            self.status.setText(_tr('Obraz przekracza limit 40 megapikseli. Wklej mniejszy fragment.'))
            return
        if not self._can_replace_document():
            return
        self._clear_document()
        self._load_image(image)
        self.file_label.setText(_tr('Obraz ze schowka · tylko lokalnie'))

    def _show_page(self, index):
        self._cancel_pdf()
        self._page_index = None
        self._pdf_index_failed = False
        self._pending_region = None
        self.selection.clear()
        self._selected_source = {}
        self._crop = QImage()
        self.ocr_btn.setEnabled(False)
        if index < 0:
            return
        if self.pdf_mode:
            self.preview_stack.setCurrentWidget(self.canvas_scroll)
            cached = self._page_cache.get(index)
            if cached is not None:
                self._page_cache.move_to_end(index)
                image, self._page_index = cached
                self.canvas.set_image(image, self._canvas_zoom(image))
                self.status.setText(_tr('Zaznacz prostokąt wokół jednego adresu lub warunku. Tekst z PDF zostanie odczytany bez OCR.'))
            else:
                self.canvas.set_image(QImage())
                self.status.setText(_tr('Przygotowuję stronę PDF w tle… Możesz zmienić stronę lub zamknąć okno.'))
                self._pending_pdf = (index, self._pdf_generation)
                self._start_pdf()
        elif self.sections:
            self.text_preview.setHtml(self.sections[index].html)
            self.preview_stack.setCurrentWidget(self.text_preview)
        elif not self.clipboard_image.isNull():
            self.canvas.set_image(self.clipboard_image, self._canvas_zoom(self.clipboard_image))
            self.preview_stack.setCurrentWidget(self.canvas_scroll)
            self.status.setText(_tr('Zaznacz obszar obrazu, następnie uruchom lokalny OCR lub wpisz treść ręcznie.'))
        self._update_progress()

    def _cancel_pdf(self):
        self._pdf_generation += 1
        self._pending_pdf = None
        self._pending_region = None
        if self._pdf_worker is not None:
            self._pdf_worker.requestInterruption()

    def _start_pdf(self):
        if self._pdf_worker is not None or self._pending_pdf is None or self._closing is not None:
            return
        page, generation = self._pending_pdf
        self._pending_pdf = None
        worker = PdfPageTask(self._pdf_path, page, generation, self._pdf_stamp, self)
        self._pdf_worker = worker
        worker.rendered.connect(self._pdf_rendered)
        worker.indexed.connect(self._pdf_indexed)
        worker.failed.connect(self._pdf_failed)
        worker.finished.connect(self._pdf_finished)
        worker.start()

    def _pdf_rendered(self, generation, image):
        if generation != self._pdf_generation or self._closing is not None:
            return
        self.canvas.set_image(image, self._canvas_zoom(image))
        self.status.setText(_tr('Strona gotowa. Przygotowuję tekst w tle; możesz już zaznaczyć obszar.'))

    def _pdf_indexed(self, generation, prepared):
        if generation != self._pdf_generation or self._closing is not None:
            return
        self._page_index = prepared
        page = self.page_combo.currentIndex()
        self._page_cache[page] = (self.canvas.image, prepared)
        self._page_cache.move_to_end(page)
        while len(self._page_cache) > 3:
            self._page_cache.popitem(last=False)
        if not self._busy():
            self.status.setText(_tr('Tekst strony gotowy. Zaznacz fragment i przypisz do pola.'))
        if self._pending_region is not None:
            region = self._pending_region
            self._pending_region = None
            self._region(region)

    def _pdf_failed(self, generation, message):
        if generation == self._pdf_generation and self._closing is None:
            self._pdf_index_failed = True
            self._pending_region = None
            self.status.setText(message)

    def _pdf_finished(self):
        worker = self._pdf_worker
        self._pdf_worker = None
        if worker is not None:
            worker.deleteLater()
        if self._closing is not None:
            result, self._closing = self._closing, None
            self.done(result)
        else:
            self._start_pdf()

    def _zoom(self):
        if self.preview_stack.currentWidget() == self.text_preview:
            font = self.text_preview.document().defaultFont()
            font.setPointSizeF(11*(self.zoom_combo.currentData() or 1))
            self.text_preview.document().setDefaultFont(font)
        elif not self.canvas.image.isNull():
            image = self.canvas.image.copy()
            self.canvas.set_image(image, self._canvas_zoom(image))

    def _canvas_zoom(self, image):
        return self.zoom_combo.currentData() or max(.1, (self.canvas_scroll.viewport().width()-24)/max(1, image.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'canvas') and not self.canvas.image.isNull() and not self.zoom_combo.currentData():
            self._zoom()

    def _source(self):
        return {'document': self.source_path.name if self.source_path else 'clipboard.png',
                'section': self.page_combo.currentText(), 'page': self.page_combo.currentIndex()+1}

    def _region(self, normalized):
        if self._busy():
            return
        self.selection.clear()
        self._selected_source = {**self._source(), 'region': [normalized.x(), normalized.y(), normalized.width(), normalized.height()]}
        image = self.canvas.image
        area = QRectF(normalized.x()*image.width(), normalized.y()*image.height(),
                      normalized.width()*image.width(), normalized.height()*image.height())
        self._crop = image.copy(area.toAlignedRect())
        self.ocr_btn.setEnabled(not self._crop.isNull())
        if self.pdf_mode:
            if self._page_index is None:
                if self._pdf_index_failed:
                    self.status.setText(_tr('Tekst strony jest niedostępny. Użyj OCR zaznaczenia lub wpisz treść ręcznie.'))
                    return
                self._pending_region = QRectF(normalized)
                self.status.setText(_tr('Przygotowuję tekst strony. Odczytam ostatnie zaznaczenie po zakończeniu.'))
                return
            size = self.pdf.pagePointSize(self.page_combo.currentIndex())
            pdf_area = QRectF(normalized.x()*size.width(), normalized.y()*size.height(),
                             normalized.width()*size.width(), normalized.height()*size.height())
            try:
                text = pdf_text_in_rect(None, self.page_combo.currentIndex(), pdf_area, self._page_index)
            except ValueError as error:
                self.status.setText(str(error))
                return
            if text:
                self.selection.setPlainText(text)
                self.status.setText(_tr('Odczytano tekst z wybranego obszaru. Sprawdź go i przypisz do pola.'))
            else:
                self.status.setText(_tr('W tym obszarze nie ma warstwy tekstowej. Użyj lokalnego OCR lub wpisz treść ręcznie.'))
        else:
            self.status.setText(_tr('Obszar zaznaczony. Użyj lokalnego OCR lub wpisz treść ręcznie.'))

    def _text_selected(self):
        if self._busy():
            return
        text = self.text_preview.textCursor().selectedText().replace('\u2029', '\n').replace('\u2028', '\n').replace('\ufdd0', '\n').replace('\ufdd1', '\n')
        if text:
            self._selected_source = self._source()
            self.selection.setPlainText(text)

    def _run_ocr(self):
        if self._crop.isNull() or self._busy():
            return
        if not readers.tesseract_path():
            self.status.setText(_tr('Nie znaleziono Tesseract OCR. Zainstaluj go lokalnie albo wpisz treść zaznaczenia ręcznie.'))
            return
        self._pending_region = None
        self._ocr = OcrTask(self._crop, self)
        self._ocr.succeeded.connect(self._ocr_ready)
        self._ocr.failed.connect(self.status.setText)
        self._ocr.finished.connect(self._background_finished)
        self._set_busy(True)
        self.status.setText(_tr('Odczyt lokalny… limit 2 GB pamięci, 45 sekund i dwa wątki. PL / EN / DE / FR / IT.'))
        self._ocr.start()

    def _ocr_ready(self, text):
        self.selection.setPlainText(text)
        self.status.setText(_tr('Sprawdź wynik OCR przed przypisaniem.') if text else _tr('OCR nie znalazł tekstu. Popraw zaznaczenie lub wpisz treść ręcznie.'))

    @Slot()
    def _background_finished(self):
        worker = self.sender()
        if self._reader is worker:
            self._reader = None
        if self._ocr is worker:
            self._ocr = None
        worker.deleteLater()
        self._set_busy(self._busy())

    def _selection_edited(self):
        self._pending_region = None
        self.assign_btn.setEnabled(bool(self.selection.toPlainText().strip()) and not self._busy())

    def _activate(self, index):
        self.current = index
        for position, (key, label) in enumerate(FIELDS):
            button = self.field_buttons[key]
            button.setChecked(position == index)
            button.setProperty('active', position == index)
            button.style().unpolish(button)
            button.style().polish(button)
        key, label = FIELDS[index]
        self.target.setText(_tr('Przypisz do: ') + _tr(label))
        self.field_scroll.ensureWidgetVisible(self.field_buttons[key])

    def _edited(self, key):
        self.values[key] = self.edits[key].toPlainText().strip()
        if key in self.sources:
            self.sources[key]['edited'] = True
        self._update_progress()

    def _update_progress(self):
        count = sum(bool(self.edits[key].toPlainText().strip()) for key, _ in FIELDS)
        self.progress.setText(f'{count} / {len(FIELDS)} · ' + _tr('pól uzupełnionych'))
        self.apply_btn.setEnabled(self._has_document and count > 0 and not self._busy())
        for key, label in FIELDS:
            source = self.sources.get(key)
            self.source_labels[key].setText((source.get('section', '') + (' · ' + _tr('poprawiono ręcznie') if source.get('edited') else '')) if source else '')

    def assign_selection(self):
        text = self.selection.toPlainText().strip()
        if not text:
            return
        key, label = FIELDS[self.current]
        existing = self.edits[key].toPlainText().strip()
        if existing and existing != text:
            if QMessageBox.question(self, _tr('Zastąp wartość'), _tr('Zastąpić obecną wartość pola: ') + _tr(label) + '?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.edits[key].setPlainText(text)
        self.sources[key] = dict(self._selected_source or self._source())
        self._update_progress()
        self.selection.clear()
        self._selected_source = {}
        self._activate(min(self.current+1, len(FIELDS)-1))
        self.status.setText(_tr('Przypisano: ') + _tr(label))

    def mapped_values(self):
        values = {key: edit.toPlainText().strip() for key, edit in self.edits.items() if edit.toPlainText().strip()}
        for key in ('load_datetime', 'unload_datetime'):
            if key in values:
                values[key] = readers.parse_import_datetime(values[key])
        if 'price' in values:
            from markus_dispatcher.core.settlement import amount
            import re
            text = values['price']
            if re.search(r'\b(?:PLN|USD|GBP|CHF)\b', text, re.I):
                raise ValueError(_tr('Pole stawki obsługuje EUR. Przelicz kwotę ręcznie lub zachowaj ją w uwagach.'))
            text = re.sub(r'(?i)\b(?:EUR|netto|net|fracht|stawka)\b|[€:]', '', text).strip().replace('\xa0', '').replace(' ', '')
            amount(text, _tr('Stawka'))
            values['price'] = text
        return values

    def _apply(self):
        if self._busy():
            return
        try:
            values = self.mapped_values()
        except ValueError as error:
            QMessageBox.warning(self, _tr('Sprawdź dane'), str(error))
            return
        if not values:
            return
        missing = [_tr(label) for key, label in FIELDS if key in REQUIRED and key not in values]
        lines = [_tr(label) + ':\n' + values[key] for key, label in FIELDS if key in values]
        message = QMessageBox(self)
        message.setWindowTitle(_tr('Sprawdź import'))
        message.setText(_tr('Przenieść wskazane dane do edytora zlecenia? Zapis do bazy wykonujesz osobno.'))
        message.setInformativeText((_tr('Do uzupełnienia: ') + ', '.join(missing) + '\n\n' if missing else '') +
            _tr('Zlecenie pozostanie lokalne. Wysyłka, geokodowanie i synchronizacja będą zablokowane. Rozwiń szczegóły, aby sprawdzić wszystkie wartości.'))
        message.setDetailedText('\n\n'.join(lines))
        message.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        message.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if message.exec() != QMessageBox.StandardButton.Ok:
            return
        self.values = values
        self._applied = True
        self.accept()

    def done(self, result):
        if self._busy():
            self.status.setText(_tr('Poczekaj na zakończenie lokalnego odczytu (maksymalnie 60 sekund).'))
            return
        self._cancel_pdf()
        if self._pdf_worker is not None:
            self._closing = result
            self.status.setText(_tr('Kończę przygotowanie strony…'))
            return
        self._page_cache.clear()
        self._pdf_lease.release()
        self._temporary.cleanup()
        super().done(result)
