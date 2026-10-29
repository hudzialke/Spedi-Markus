from __future__ import annotations

from spedi.i18n import tr as _tr

from pathlib import Path
import json

from PySide6.QtCore import QBuffer, QDate, QDateTime, QIODevice, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from markus_dispatcher.config import ARCHIVE_ROOT, EMAIL_COMPOSE_MODE
from markus_dispatcher.core.browser_launch import open_web_url
from markus_dispatcher.core.footer_store import load_footer, save_footer
from markus_dispatcher.core.geocode import extract_pasted_gps
from markus_dispatcher.core.instruction import build_instruction
from markus_dispatcher.core.job_archive import (
    copy_file_attachment,
    detect_kind,
    write_sheets_shortcut,
)
from markus_dispatcher.core.maps_link import build_maps_dir_url
from markus_dispatcher.core.models import (
    GLYPH_DETAILS,
    GLYPH_LOAD,
    GLYPH_UNLOAD,
    JOB_STATUS_LABELS_PL,
    Coordinates,
    JobData,
)
from markus_dispatcher.core.notify import (
    default_subject,
    email_compose_url,
    whatsapp_url,
)
from markus_dispatcher.core.pod import (
    save_image_bytes_as_pod_pdf,
    save_image_file_as_pod_pdf,
)
from markus_dispatcher.core.summary import build_client_status_message
from markus_dispatcher.data import db, monthly_settlements
from markus_dispatcher.ui.footer_dialog import FooterDialog
from markus_dispatcher.ui.geocode_worker import start_geocode
from markus_dispatcher.ui.icons import tool_button
from markus_dispatcher.ui.settlement_panel import SettlementPanel


def _format_dt(edit: QDateTimeEdit) -> str:
    if edit.specialValueText() and edit.dateTime() == edit.minimumDateTime():
        return ''
    return edit.dateTime().toPython().strftime("%d.%m.%Y %H:%M")


def _parse_dt(text: str) -> QDateTime | None:
    text = text.strip()
    if not text:
        return None
    parsed = QDateTime.fromString(text, "dd.MM.yyyy HH:mm")
    return parsed if parsed.isValid() else None


class StageBlock(QWidget):
    def __init__(self, glyph: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        g = QLabel(glyph)
        g.setObjectName("GlyphLabel")
        g.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        row.addWidget(g)
        col = QVBoxLayout()
        head = QLabel(title)
        head.setObjectName("TileTitle")
        col.addWidget(head)
        self.body = QVBoxLayout()
        col.addLayout(self.body, stretch=1)
        row.addLayout(col, stretch=1)


class JobPane(QWidget):
    """Prawa szpalta — edycja zlecenia + update klienta."""

    saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._job_id: int | None = None
        self._local_only = False
        self._import_sources = ''
        self._pending_import_documents: list[tuple[Path | None, QImage]] = []
        self._driver_id: int | None = None
        self._client_id: int | None = None
        self._archive_path = ""
        self._loading_history = False
        self._footer_text = load_footer()
        self._load_coords: Coordinates | None = None
        self._unload_coords: Coordinates | None = None
        self._maps_url = ""
        self._load_thread = None
        self._unload_thread = None
        self._geocode_gen = {"load": 0, "unload": 0}
        self._pending_geocode = {"load": None, "unload": None}
        self._preview_dirty = False
        self._updating_preview = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.title = QLabel(_tr("Zlecenie"))
        self.title.setObjectName("TileTitle")
        head.addWidget(self.title)
        self.history_combo = QComboBox()
        self.history_combo.setMinimumWidth(200)
        self.history_combo.setToolTip(_tr("Ewidencja zleceń kierowcy (chronologicznie)"))
        self.history_combo.currentIndexChanged.connect(self._on_history_picked)
        head.addWidget(self.history_combo, stretch=1)
        self.status_combo = QComboBox()
        for key, label in JOB_STATUS_LABELS_PL.items():
            self.status_combo.addItem(label, key)
        head.addWidget(self.status_combo)
        self.save_btn = tool_button(_tr("Zapisz zlecenie"), "save", kind="primary")
        self.save_btn.clicked.connect(self._save)
        self.copy_btn = tool_button(_tr("Kopiuj instrukcję"), "copy")
        self.copy_btn.clicked.connect(self._copy_instruction)
        self.maps_btn = tool_button(_tr("Otwórz Google Maps"), "maps")
        self.maps_btn.clicked.connect(self._open_maps)
        self.footer_btn = tool_button(_tr("Dopisek instrukcji"), "note")
        self.footer_btn.clicked.connect(self._edit_footer)
        head.addWidget(self.save_btn)
        head.addWidget(self.copy_btn)
        head.addWidget(self.maps_btn)
        head.addWidget(self.footer_btn)
        self.import_btn = tool_button(_tr('Import z dokumentu'), 'file', label=_tr('Import z dokumentu'))
        self.import_btn.clicked.connect(self._import_document)
        head.addWidget(self.import_btn)
        root.addLayout(head)

        month_row = QHBoxLayout()
        month_row.addWidget(QLabel(_tr("Miesiąc rozładunku")))
        self.month_edit = QDateEdit(QDate.currentDate())
        self.month_edit.setDisplayFormat("MM.yyyy")
        self.month_edit.setToolTip(_tr("Historia zleceń wybranego kierowcy według miesiąca rozładunku. Zmiana filtra nie usuwa danych."))
        self.month_edit.dateChanged.connect(self._on_month_changed)
        month_row.addWidget(self.month_edit)
        month_row.addWidget(QLabel(_tr("Zlecenia z innych miesięcy zobaczysz po zmianie miesiąca.")))
        month_row.addStretch(1)
        root.addLayout(month_row)

        self.import_hint = QLabel()
        self.import_hint.setWordWrap(True)
        self.import_hint.setStyleSheet('color: #7a5000; background: #fff4d6; padding: 8px;')
        self.import_hint.hide()
        root.addWidget(self.import_hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)

        load_tile = QFrame()
        load_tile.setObjectName("Tile")
        load_l = QVBoxLayout(load_tile)
        load_l.setContentsMargins(14, 14, 14, 14)
        load = StageBlock(GLYPH_LOAD, _tr("Załadunek"))
        self.load_address = QPlainTextEdit()
        self.load_address.setPlaceholderText(_tr("Adres załadunku…"))
        self.load_address.setMinimumHeight(140)
        self.load_when = QDateTimeEdit(QDateTime.currentDateTime())
        self.load_when.setCalendarPopup(True)
        self.load_when.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.load_when.setMinimumDateTime(QDateTime(QDate(1900, 1, 1), self.load_when.minimumTime()))
        self.load_when.setSpecialValueText(_tr('Nie wskazano terminu'))
        self.load_gps = QLabel("GPS: —")
        self.load_status = QLabel("")
        self.load_geo_btn = tool_button(
            _tr("Szukaj współrzędnych w Nominatim"), "search"
        )
        self.load_geo_btn.clicked.connect(lambda: self._request_geocode("load"))
        load_gps_row = QHBoxLayout()
        load_gps_row.addWidget(self.load_gps, stretch=1)
        load_gps_row.addWidget(self.load_geo_btn)
        load.body.addWidget(self.load_address, stretch=1)
        load.body.addWidget(self.load_when)
        load.body.addLayout(load_gps_row)
        load.body.addWidget(self.load_status)
        load_l.addWidget(load)
        grid.addWidget(load_tile, 0, 0)

        unload_tile = QFrame()
        unload_tile.setObjectName("Tile")
        unload_l = QVBoxLayout(unload_tile)
        unload_l.setContentsMargins(14, 14, 14, 14)
        unload = StageBlock(GLYPH_UNLOAD, _tr("Rozładunek"))
        self.unload_address = QPlainTextEdit()
        self.unload_address.setPlaceholderText(_tr("Adres rozładunku…"))
        self.unload_address.setMinimumHeight(140)
        self.unload_when = QDateTimeEdit(QDateTime.currentDateTime())
        self.unload_when.setCalendarPopup(True)
        self.unload_when.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.unload_when.setMinimumDateTime(QDateTime(QDate(1900, 1, 1), self.unload_when.minimumTime()))
        self.unload_when.setSpecialValueText(_tr('Nie wskazano terminu'))
        self.unload_gps = QLabel("GPS: —")
        self.unload_status = QLabel("")
        self.unload_geo_btn = tool_button(
            _tr("Szukaj współrzędnych w Nominatim"), "search"
        )
        self.unload_geo_btn.clicked.connect(lambda: self._request_geocode("unload"))
        unload_gps_row = QHBoxLayout()
        unload_gps_row.addWidget(self.unload_gps, stretch=1)
        unload_gps_row.addWidget(self.unload_geo_btn)
        unload.body.addWidget(self.unload_address, stretch=1)
        unload.body.addWidget(self.unload_when)
        unload.body.addLayout(unload_gps_row)
        unload.body.addWidget(self.unload_status)
        unload_l.addWidget(unload)
        grid.addWidget(unload_tile, 0, 1)

        details_tile = QFrame()
        details_tile.setObjectName("Tile")
        details_l = QVBoxLayout(details_tile)
        details_l.setContentsMargins(14, 14, 14, 14)
        details = StageBlock(GLYPH_DETAILS, _tr("Szczegóły"))
        form = QFormLayout()
        self.ref_edit = QLineEdit()
        self.ref_edit.setPlaceholderText(_tr("Nazwa / numer zlecenia…"))
        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText(_tr("Nazwa klienta…"))
        self.pallets_edit = QLineEdit()
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMinimumHeight(80)
        form.addRow(_tr("Nazwa"), self.ref_edit)
        form.addRow(_tr("Klient"), self.client_edit)
        form.addRow(_tr("Palety"), self.pallets_edit)
        form.addRow(_tr("Uwagi"), self.notes_edit)
        details.body.addLayout(form)
        details_l.addWidget(details)
        grid.addWidget(details_tile, 1, 0)

        side_tile = QFrame()
        side_tile.setObjectName("Tile")
        side_l = QVBoxLayout(side_tile)
        side_l.setContentsMargins(14, 14, 14, 14)
        docs_lbl = QLabel(_tr("Dokumenty / POD"))
        docs_lbl.setObjectName("TileTitle")
        side_l.addWidget(docs_lbl)
        self.docs_list = QListWidget()
        self.docs_list.setMinimumHeight(88)
        self.docs_list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        side_l.addWidget(self.docs_list)
        docs_btns = QHBoxLayout()
        add_file_btn = tool_button(_tr("Dodaj PDF/Excel"), "file")
        add_file_btn.clicked.connect(self._add_file_attachment)
        add_sheets_btn = tool_button(_tr("Dodaj Google Sheets"), "grid")
        add_sheets_btn.clicked.connect(self._add_sheets_link)
        add_pod_btn = tool_button(_tr("Dodaj POD (fotka)"), "camera")
        add_pod_btn.clicked.connect(self._add_pod_files)
        paste_pod_btn = tool_button(_tr("POD ze schowka"), "clipboard", kind="primary")
        paste_pod_btn.clicked.connect(self._paste_pod_from_clipboard)
        open_folder_btn = tool_button(_tr("Otwórz folder ewidencji"), "folder")
        open_folder_btn.clicked.connect(self._open_archive_folder)
        open_doc_btn = tool_button(_tr("Otwórz wybrany plik"), "open")
        open_doc_btn.clicked.connect(self._open_selected_doc)
        docs_btns.addWidget(add_file_btn)
        docs_btns.addWidget(add_sheets_btn)
        docs_btns.addWidget(add_pod_btn)
        docs_btns.addWidget(paste_pod_btn)
        docs_btns.addWidget(open_doc_btn)
        docs_btns.addWidget(open_folder_btn)
        docs_btns.addStretch(1)
        side_l.addLayout(docs_btns)
        self.archive_hint = QLabel(_tr("Każde zdjęcie POD = osobny PDF. Ctrl+V też wkleja POD."))
        self.archive_hint.setWordWrap(True)
        self.archive_hint.setStyleSheet("color: #5a6672; font-size: 13px;")
        side_l.addWidget(self.archive_hint)

        pod_title = QLabel(_tr('Wymagania POD po rozładunku'))
        pod_title.setObjectName('TileTitle')
        side_l.addWidget(pod_title)
        self.pod_requirements_edit = QPlainTextEdit()
        self.pod_requirements_edit.setAccessibleName(_tr('Wymagania POD po rozładunku'))
        self.pod_requirements_edit.setPlaceholderText(_tr('Dokumenty, format, adres / portal, termin, kara. Zachowaj pełną treść warunków.'))
        self.pod_requirements_edit.setMinimumHeight(90)
        side_l.addWidget(self.pod_requirements_edit)

        client_lbl = QLabel(_tr("Update do klienta"))
        client_lbl.setObjectName("TileTitle")
        side_l.addWidget(client_lbl)
        self.client_phone = QLineEdit()
        self.client_phone.setPlaceholderText(_tr("Telefon WhatsApp…"))
        self.client_email = QLineEdit()
        self.client_email.setPlaceholderText("E-mail…")
        self.client_phone.editingFinished.connect(self._persist_contacts)
        self.client_email.editingFinished.connect(self._persist_contacts)
        side_l.addWidget(self.client_phone)
        side_l.addWidget(self.client_email)
        notify_row = QHBoxLayout()
        self.wa_btn = tool_button(_tr("Wyślij WhatsApp"), "chat", kind="wa")
        self.wa_btn.clicked.connect(self._send_whatsapp)
        self.mail_btn = tool_button(_tr("Wyślij e-mail"), "mail", kind="mail")
        self.mail_btn.clicked.connect(self._send_email)
        self.reset_preview_btn = tool_button(
            _tr("Przywróć szablon notatki (z potwierdzeniem)"), "undo"
        )
        self.reset_preview_btn.clicked.connect(self._reset_preview_template)
        notify_row.addWidget(self.wa_btn)
        notify_row.addWidget(self.mail_btn)
        notify_row.addWidget(self.reset_preview_btn)
        notify_row.addStretch(1)
        side_l.addLayout(notify_row)
        self.preview = QPlainTextEdit()
        self.preview.setPlaceholderText("Update:\nKierowca: …\nTrasa: …\nStatus: …")
        self.preview.setMinimumHeight(120)
        self.preview.textChanged.connect(self._on_preview_edited)
        side_l.addWidget(self.preview, stretch=1)
        grid.addWidget(side_tile, 1, 1)

        self.settlement = SettlementPanel(self)
        grid.addWidget(self.settlement, 2, 0, 1, 2)

        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(host)
        root.addWidget(scroll, stretch=1)

        self.load_address.textChanged.connect(self._on_load_changed)
        self.unload_address.textChanged.connect(self._on_unload_changed)
        for w in (
            self.load_when,
            self.unload_when,
            self.ref_edit,
            self.client_edit,
            self.pallets_edit,
            self.notes_edit,
            self.status_combo,
        ):
            if hasattr(w, "textChanged"):
                w.textChanged.connect(self._refresh_preview)  # type: ignore[attr-defined]
            elif hasattr(w, "dateTimeChanged"):
                w.dateTimeChanged.connect(self._refresh_preview)  # type: ignore[attr-defined]
            elif hasattr(w, "currentIndexChanged"):
                w.currentIndexChanged.connect(self._refresh_preview)  # type: ignore[attr-defined]

        self.unload_when.dateTimeChanged.connect(lambda _dt: self.settlement.apply_month_target() if not self._loading_history else None)
        for widget in (self.ref_edit, self.client_edit, self.load_address, self.unload_address, self.pod_requirements_edit):
            widget.textChanged.connect(self._refresh_import_hint)
        self.load_when.dateTimeChanged.connect(self._refresh_import_hint)
        self.unload_when.dateTimeChanged.connect(self._refresh_import_hint)
        self.new_job()

    def _import_document(self):
        if self._driver_id is None:
            QMessageBox.information(self, _tr('Import z dokumentu'), _tr('Najpierw wybierz kierowcę z floty. Importowane zlecenie trafi do jego ewidencji.'))
            return
        from markus_dispatcher.ui.document_import_dialog import DocumentImportDialog
        dialog = DocumentImportDialog(self)
        try:
            if dialog.exec() != DocumentImportDialog.DialogCode.Accepted:
                return
            if any((self.ref_edit.text().strip(), self.client_edit.text().strip(),
                    self.load_address.toPlainText().strip(), self.unload_address.toPlainText().strip(),
                    self.pod_requirements_edit.toPlainText().strip())):
                if QMessageBox.question(self, _tr('Przenieś dane'),
                    _tr('Przenieść wskazane pola do bieżącego zlecenia? Pola wskazane w importerze zastąpią ich obecne wartości.'),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
            self._apply_document_import(dialog.values, dialog.sources, dialog.source_path, dialog.clipboard_image)
        finally:
            dialog.deleteLater()

    def _apply_document_import(self, values, sources, source_path=None, clipboard_image=None):
        self._cancel_geocode()
        self._local_only = True
        # Do not update/create a shared client from data designated as local-only.
        self._client_id = None
        self._loading_history = True
        try:
            for key, edit in (('ref_numbers', self.ref_edit), ('client_name', self.client_edit),
                              ('pallets', self.pallets_edit)):
                if key in values:
                    edit.setText(values[key])
            for key, edit in (('load_address', self.load_address), ('unload_address', self.unload_address),
                              ('pod_requirements', self.pod_requirements_edit), ('notes', self.notes_edit)):
                if key in values:
                    edit.setPlainText(values[key])
            # Never keep today's default date as if it came from the document.
            for key, edit in (('load_datetime', self.load_when), ('unload_datetime', self.unload_when)):
                if key in values:
                    self._apply_datetime(edit, values[key])
                elif self._job_id is None:
                    edit.setDateTime(edit.minimumDateTime())
            if values.get('driver_note'):
                notes = self.notes_edit.toPlainText().strip()
                self.notes_edit.setPlainText((notes + '\n' if notes else '') + _tr('Oznaczenie z dokumentu: ') + values['driver_note'])
            if 'price' in values:
                self.settlement.fields['price'].setText(values['price'])
            for side in ('load', 'unload'):
                if side + '_address' in values:
                    self._set_coords(side, None)
            self._maps_url = ''
            previous = {}
            try:
                previous = json.loads(self._import_sources) if self._import_sources else {}
            except (TypeError, ValueError):
                pass
            self._import_sources = json.dumps({**previous, **sources}, ensure_ascii=False)
            if source_path is not None:
                self._pending_import_documents.append((Path(source_path), QImage()))
            elif clipboard_image is not None and not clipboard_image.isNull():
                self._pending_import_documents.append((None, clipboard_image.copy()))
        finally:
            self._loading_history = False
        self._refresh_preview()
        self._refresh_import_hint()

    def _refresh_import_hint(self, *args):
        if self._loading_history:
            return
        self.import_hint.setVisible(self._local_only)
        if not self._local_only:
            return
        missing = []
        for text, label in ((self.ref_edit.text(), _tr('numer')), (self.client_edit.text(), _tr('klient')),
                            (self.load_address.toPlainText(), _tr('załadunek')),
                            (self.unload_address.toPlainText(), _tr('rozładunek')),
                            (_format_dt(self.load_when), _tr('termin załadunku')),
                            (_format_dt(self.unload_when), _tr('termin rozładunku')),
                            (self.pod_requirements_edit.toPlainText(), _tr('wymagania POD'))):
            if not text.strip():
                missing.append(label)
        self.import_hint.setText((_tr('⚠ Do uzupełnienia: ') + ', '.join(missing) + '. ' if missing else '') +
            _tr('Import lokalny — wysyłka i synchronizacja zablokowane. ') +
            (_tr('Dokument źródłowy zostanie dołączony przy zapisie zlecenia.') if self._pending_import_documents else ''))

    def _allow_external(self):
        if not self._local_only:
            return True
        QMessageBox.information(self, _tr('Dane lokalne'),
            _tr('To zlecenie pochodzi z lokalnego importu. Wysyłka danych, geokodowanie i synchronizacja są zablokowane.'))
        return False

    def _archive_import_documents(self, saved):
        if not self._pending_import_documents or not saved.archive_path:
            return
        docs = ARCHIVE_ROOT / saved.archive_path / 'dokumenty'
        while self._pending_import_documents:
            path, image = self._pending_import_documents[0]
            try:
                if path is not None:
                    destination = copy_file_attachment(docs, path)
                    original = path.name
                else:
                    from markus_dispatcher.core.job_archive import next_document_index
                    docs.mkdir(parents=True, exist_ok=True)
                    destination = docs / f'{next_document_index(docs):02d}_dokument_ze_schowka.png'
                    if not image.save(str(destination), 'PNG'):
                        raise OSError('image save failed')
                    original = 'clipboard.png'
                try:
                    db.add_attachment(saved.id, kind=detect_kind(destination), original_name=original, stored_name=destination.name)
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
            except Exception:
                QMessageBox.warning(self, _tr('Dokument źródłowy'),
                    _tr('Dane zlecenia zapisano, ale nie udało się dołączyć dokumentu źródłowego. Sprawdź dostęp do pliku i zapisz zlecenie ponownie.'))
                break
            self._pending_import_documents.pop(0)
        self._refresh_import_hint()

    def selected_month(self) -> str:
        return self.month_edit.date().toString("yyyy-MM")

    def _on_month_changed(self, _date) -> None:
        if self._loading_history:
            return
        if self._job_id is not None:
            self._save(show_message=False)
        elif self._driver_id is not None and (self.ref_edit.text().strip() or self.load_address.toPlainText().strip() or self.unload_address.toPlainText().strip()):
            self._save(show_message=False)
        self.bind_driver(self._driver_id)

    def bind_driver(self, driver_id: int | None) -> None:
        self._cancel_geocode()
        self._driver_id = driver_id
        if driver_id is None:
            self.new_job()
            return
        matching = monthly_settlements.jobs_for_month(driver_id, self.selected_month())
        if not matching:
            matching = [job for job in db.list_jobs_for_driver(driver_id)
                        if job.local_only and not job.unload_datetime]
        existing = matching[-1] if matching else None
        if existing is not None:
            self.load_job(existing)
        else:
            self.new_job(driver_id=driver_id)

    def new_job(self, *, driver_id: int | None = None) -> None:
        if self._job_id is not None:
            self.settlement.save_draft(self._job_id)
        self.settlement.load_job(None)
        self._cancel_geocode()
        self._loading_history = True
        self._job_id = None
        self._local_only = False
        self._import_sources = ''
        self._pending_import_documents.clear()
        self.pod_requirements_edit.clear()
        self._driver_id = driver_id
        self._client_id = None
        self._archive_path = ""
        self.ref_edit.clear()
        self.client_edit.clear()
        self.pallets_edit.clear()
        self.notes_edit.clear()
        self.client_phone.clear()
        self.client_email.clear()
        self.load_address.blockSignals(True)
        self.unload_address.blockSignals(True)
        self.load_address.clear()
        self.unload_address.clear()
        self.load_address.blockSignals(False)
        self.unload_address.blockSignals(False)
        now = QDateTime.currentDateTime()
        if now.toString("yyyy-MM") != self.selected_month():
            now.setDate(QDate(self.month_edit.date().year(), self.month_edit.date().month(), 1))
        self.load_when.setDateTime(now)
        self.unload_when.setDateTime(now)
        self._set_coords("load", None)
        self._set_coords("unload", None)
        self.load_status.setText("")
        self.unload_status.setText("")
        self.status_combo.setCurrentIndex(
            self.status_combo.findData("assigned" if driver_id else "draft")
        )
        self.title.setText(_tr("Zlecenie · nowe"))
        self._set_preview_text(
            build_client_status_message(
                self.collect(), driver_name=self._driver_label()
            ),
            dirty=False,
        )
        self._maps_url = ""
        self._reload_history()
        self._reload_attachments()
        self._loading_history = False
        self.settlement.apply_month_target()

        self._refresh_import_hint()

    def load_job(self, job: JobData) -> None:
        if self._job_id is not None:
            self.settlement.save_draft(self._job_id)
        self.settlement.load_job(job.id)
        self._cancel_geocode()
        self._loading_history = True
        self._job_id = job.id
        self._local_only = job.local_only
        self._import_sources = job.import_sources
        self._pending_import_documents.clear()
        self.pod_requirements_edit.setPlainText(job.pod_requirements)
        self._driver_id = job.driver_id
        self._client_id = job.client_id
        self._archive_path = job.archive_path
        self.ref_edit.setText(job.ref_numbers)
        self.client_edit.setText(job.client_name)
        self.pallets_edit.setText(job.pallets)
        self.notes_edit.setPlainText(job.notes)
        self.load_address.blockSignals(True)
        self.unload_address.blockSignals(True)
        self.load_address.setPlainText(job.load_address)
        self.unload_address.setPlainText(job.unload_address)
        self.load_address.blockSignals(False)
        self.unload_address.blockSignals(False)
        self._apply_datetime(self.load_when, job.load_datetime)
        self._apply_datetime(self.unload_when, job.unload_datetime)
        self._set_coords("load", job.load_coords)
        self._set_coords("unload", job.unload_coords)
        if not job.load_coords:
            self.load_status.setText("" if not job.load_address else _tr("Brak GPS"))
        if not job.unload_coords:
            self.unload_status.setText(
                "" if not job.unload_address else _tr("Brak GPS")
            )
        self._maps_url = job.maps_url
        idx = self.status_combo.findData(job.status)
        if idx >= 0:
            self.status_combo.setCurrentIndex(idx)
        if job.client_id:
            clients = {c.id: c for c in db.list_clients()}
            client = clients.get(job.client_id)
            if client:
                # Kontakt ze zlecenia ma pierwszeństwo; klient = fallback.
                self.client_phone.setText(job.contact_phone or client.phone)
                self.client_email.setText(job.contact_email or client.email)
            else:
                self.client_phone.setText(job.contact_phone)
                self.client_email.setText(job.contact_email)
        else:
            self.client_phone.setText(job.contact_phone)
            self.client_email.setText(job.contact_email)
        ref = job.ref_numbers or (f"#{job.id}" if job.id else "…")
        self.title.setText(f"{_tr('Zlecenie · ')}{ref}")
        template = build_client_status_message(
            job, driver_name=self._driver_label()
        )
        saved_msg = job.client_message.strip()
        if saved_msg and saved_msg != template.strip():
            self._set_preview_text(job.client_message, dirty=True)
        else:
            self._set_preview_text(saved_msg or template, dirty=False)
        self._reload_history(select_job_id=job.id)
        self._reload_attachments()
        self._loading_history = False
        self.settlement.apply_month_target()
        self._refresh_import_hint()

    def collect(self) -> JobData:
        load_c = self._load_coords
        unload_c = self._unload_coords
        maps = ""
        if load_c and unload_c:
            maps = build_maps_dir_url(load_c, unload_c)
        status = self.status_combo.currentData()
        assert isinstance(status, str)
        return JobData(
            id=self._job_id,
            driver_id=self._driver_id,
            client_id=self._client_id,
            client_name=self.client_edit.text().strip(),
            ref_numbers=self.ref_edit.text().strip(),
            pallets=self.pallets_edit.text().strip(),
            notes=self.notes_edit.toPlainText().strip(),
            load_address=self.load_address.toPlainText().strip(),
            load_datetime=_format_dt(self.load_when),
            load_coords=load_c,
            unload_address=self.unload_address.toPlainText().strip(),
            unload_datetime=_format_dt(self.unload_when),
            unload_coords=unload_c,
            maps_url=maps,
            status=status,  # type: ignore[arg-type]
            archive_path=self._archive_path,
            contact_phone=self.client_phone.text().strip(),
            contact_email=self.client_email.text().strip(),
            client_message=self.preview.toPlainText(),
            local_only=self._local_only,
            pod_requirements=self.pod_requirements_edit.toPlainText().strip(),
            import_sources=self._import_sources,
        )

    def _apply_datetime(self, edit: QDateTimeEdit, text: str) -> None:
        parsed = _parse_dt(text)
        if parsed is not None:
            edit.setDateTime(parsed)
        else:
            edit.setDateTime(edit.minimumDateTime())

    def _on_load_changed(self) -> None:
        self._on_address_changed("load")

    def _on_unload_changed(self) -> None:
        self._on_address_changed("unload")

    def _on_address_changed(self, side: str) -> None:
        if self._loading_history:
            return
        self._pending_geocode[side] = None
        address_edit = self.load_address if side == "load" else self.unload_address
        status_lbl = self.load_status if side == "load" else self.unload_status
        address = address_edit.toPlainText().strip()
        pasted = extract_pasted_gps(address) if address else None
        if pasted is not None:
            self._geocode_gen[side] += 1
            self._set_coords(side, pasted)
            status_lbl.setText(_tr("OK (z wklejki)"))
            status_lbl.setStyleSheet("")
            self._refresh_preview()
            return
        self._geocode_gen[side] += 1
        self._set_coords(side, None)
        if address:
            status_lbl.setText(_tr("Kliknij lupę, żeby znaleźć GPS"))
        else:
            status_lbl.setText("")
        status_lbl.setStyleSheet("")
        self._refresh_preview()

    def _set_coords(self, side: str, coords: Coordinates | None) -> None:
        if side == "load":
            self._load_coords = coords
            self.load_gps.setText(
                f"GPS: {coords.format()}" if coords else "GPS: —"
            )
            if coords:
                self.load_status.setText("OK")
                self.load_status.setStyleSheet("")
        else:
            self._unload_coords = coords
            self.unload_gps.setText(
                f"GPS: {coords.format()}" if coords else "GPS: —"
            )
            if coords:
                self.unload_status.setText("OK")
                self.unload_status.setStyleSheet("")

    def _cancel_geocode(self) -> None:
        """Unieważnia trwające geokodowanie (zmiana kierowcy / zlecenia)."""
        self._geocode_gen["load"] += 1
        self._geocode_gen["unload"] += 1
        self._pending_geocode = {"load": None, "unload": None}
        for thread in (self._load_thread, self._unload_thread):
            if thread is not None and thread.isRunning():
                thread.quit()

    def _request_geocode(self, side: str) -> None:
        if not self._allow_external():
            return
        if self._loading_history:
            return
        address_edit = self.load_address if side == "load" else self.unload_address
        status_lbl = self.load_status if side == "load" else self.unload_status
        address = address_edit.toPlainText().strip()
        if not address:
            self._set_coords(side, None)
            status_lbl.setText("")
            status_lbl.setStyleSheet("")
            self._refresh_preview()
            return
        pasted = extract_pasted_gps(address)
        if pasted is not None:
            self._geocode_gen[side] += 1
            self._set_coords(side, pasted)
            status_lbl.setText(_tr("OK (z wklejki)"))
            status_lbl.setStyleSheet("")
            self._refresh_preview()
            return
        active = self._load_thread if side == 'load' else self._unload_thread
        if active is not None and (
            self._pending_geocode[side] == address or (
                active.property('geocode_address') == address
                and active.property('geocode_generation') == self._geocode_gen[side])):
            return
        self._geocode_gen[side] += 1
        gen = self._geocode_gen[side]
        status_lbl.setText(_tr("Szukam współrzędnych…"))
        status_lbl.setStyleSheet("")
        if active is not None:
            # Keep only the latest explicit request until the current HTTP call
            # exits; quit() cannot interrupt that call or its retry delays.
            self._pending_geocode[side] = address
            return
        thread = start_geocode(self, side, address)
        thread.setProperty('geocode_side', side)
        thread.setProperty('geocode_address', address)
        thread.setProperty('geocode_generation', gen)
        thread.finished.connect(self._geocode_finished)
        worker = thread._worker  # type: ignore[attr-defined]

        def on_ok(_side: str, lat: float, lon: float) -> None:
            if self._geocode_gen[side] != gen:
                return
            self._set_coords(side, Coordinates(lat=lat, lon=lon))
            self._refresh_preview()

        def on_fail(_side: str, message: str) -> None:
            if self._geocode_gen[side] != gen:
                return
            self._set_coords(side, None)
            status_lbl.setText(message)
            status_lbl.setStyleSheet("color: #a33;")
            self._refresh_preview()

        worker.finished.connect(on_ok, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(on_fail, Qt.ConnectionType.QueuedConnection)
        if side == "load":
            self._load_thread = thread
        else:
            self._unload_thread = thread
        thread.start()

    @Slot()
    def _geocode_finished(self):
        thread = self.sender()
        side = thread.property('geocode_side')
        if side == 'load':
            self._load_thread = None
        else:
            self._unload_thread = None
        address, self._pending_geocode[side] = self._pending_geocode[side], None
        thread._worker = None
        thread.deleteLater()
        edit = self.load_address if side == 'load' else self.unload_address
        if address is not None and edit.toPlainText().strip() == address:
            self._request_geocode(side)

    def _refresh_preview(self) -> None:
        if self._loading_history:
            return
        data = self.collect()
        self._maps_url = data.maps_url
        if self._preview_dirty:
            return
        self._set_preview_text(
            build_client_status_message(data, driver_name=self._driver_label()),
            dirty=False,
        )

    def _set_preview_text(self, text: str, *, dirty: bool) -> None:
        if self.preview.toPlainText() == text:
            self._preview_dirty = dirty
            return
        self._updating_preview = True
        self.preview.setPlainText(text)
        self._updating_preview = False
        self._preview_dirty = dirty

    def _on_preview_edited(self) -> None:
        if self._updating_preview or self._loading_history:
            return
        self._preview_dirty = True

    def _reset_preview_template(self) -> None:
        if self._preview_dirty:
            answer = QMessageBox.question(
                self,
                "MarkusDispatcher",
                _tr("Masz zmienioną notatkę do klienta.\n"
                "Nadpisać ją świeżym szablonem z danych zlecenia?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        data = self.collect()
        self._maps_url = data.maps_url
        self._set_preview_text(
            build_client_status_message(data, driver_name=self._driver_label()),
            dirty=False,
        )

    def _driver_label(self) -> str:
        if self._driver_id is None:
            return ""
        driver = db.get_driver(self._driver_id)
        return driver.display_name if driver else ""

    def _save(self, checked=False, *, show_message: bool = True) -> None:
        job = self.collect()
        if job.client_name and not job.local_only:
            client = db.find_or_create_client_by_name(job.client_name)
            if client is not None:
                job.client_id = client.id
                self._client_id = client.id
                db.upsert_client(
                    client_id=client.id,
                    name=client.name,
                    email=self.client_email.text().strip() or client.email,
                    phone=self.client_phone.text().strip() or client.phone,
                    preferred_channel=client.preferred_channel,
                    notes=client.notes,
                )
        if job.driver_id is None:
            QMessageBox.warning(
                self,
                "MarkusDispatcher",
                _tr("Wybierz kierowcę z floty — ewidencja jest per kierowca."),
            )
            return
        saved = db.save_job(job)
        self.settlement.save_draft(saved.id)
        saved = db.sync_job_to_archive(
            saved, instruction_footer=self._footer_text
        )
        self._job_id = saved.id
        self._archive_path = saved.archive_path
        self._archive_import_documents(saved)
        if self._driver_id and saved.status in ("assigned", "loading", "en_route"):
            db.set_driver_status(self._driver_id, "working")
        elif self._driver_id and saved.status == "done":
            db.set_driver_status(self._driver_id, "empty")
        self.title.setText(
            f"{_tr('Zlecenie · ')}{saved.ref_numbers or f'#{saved.id}'}"
        )
        self._reload_history(select_job_id=saved.id)
        self._reload_attachments()
        self.saved.emit()
        if show_message:
            QMessageBox.information(
                self, "MarkusDispatcher", _tr("Zapisano zlecenie i folder ewidencji."),
            )

    def _copy_instruction(self) -> None:
        text = build_instruction(self.collect(), footer=self._footer_text)
        if not text.strip():
            QMessageBox.information(self, "MarkusDispatcher", _tr("Brak treści."))
            return
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(
            self, "MarkusDispatcher", _tr("Instrukcja skopiowana do schowka.")
        )

    def _open_maps(self) -> None:
        if not self._allow_external():
            return
        url = self._maps_url or self.collect().maps_url
        if not url:
            QMessageBox.information(
                self, "MarkusDispatcher", _tr("Potrzebne obie pary współrzędnych.")
            )
            return
        QDesktopServices.openUrl(QUrl(url))

    def _edit_footer(self) -> None:
        dialog = FooterDialog(self._footer_text, parent=self)
        if dialog.exec() != FooterDialog.DialogCode.Accepted:
            return
        self._footer_text = dialog.footer_text()
        save_footer(self._footer_text)

    def _status_message(self) -> str:
        text = self.preview.toPlainText().strip()
        if text:
            return text
        return build_client_status_message(
            self.collect(), driver_name=self._driver_label()
        )

    def _persist_contacts(self) -> None:
        """Zapisuje telefon/e-mail do zlecenia (i klienta), bez pełnego komunikatu."""
        if self._driver_id is None and self._job_id is None:
            return
        job = self.collect()
        if not job.contact_phone and not job.contact_email and job.id is None:
            return
        if job.client_name and not job.local_only:
            client = db.find_or_create_client_by_name(job.client_name)
            if client is not None:
                job.client_id = client.id
                self._client_id = client.id
                db.upsert_client(
                    client_id=client.id,
                    name=client.name,
                    email=job.contact_email or client.email,
                    phone=job.contact_phone or client.phone,
                    preferred_channel=client.preferred_channel,
                    notes=client.notes,
                )
        if job.driver_id is None and job.id is None:
            return
        saved = db.save_job(job)
        if saved.driver_id is not None:
            saved = db.sync_job_to_archive(
                saved, instruction_footer=self._footer_text
            )
        self._job_id = saved.id
        self._archive_path = saved.archive_path
        self._reload_history(select_job_id=saved.id)

    def _send_whatsapp(self) -> None:
        if not self._allow_external():
            return
        self._persist_contacts()
        msg = self._status_message()
        url = whatsapp_url(self.client_phone.text(), msg)
        QGuiApplication.clipboard().setText(msg)
        open_web_url(url)

    def _send_email(self) -> None:
        if not self._allow_external():
            return
        email = self.client_email.text().strip()
        if not email:
            QMessageBox.information(
                self, "MarkusDispatcher", _tr("Podaj e-mail klienta.")
            )
            return
        self._persist_contacts()
        job = self.collect()
        url = email_compose_url(
            email,
            subject=default_subject(job.ref_numbers),
            body=self._status_message(),
            mode=EMAIL_COMPOSE_MODE,  # type: ignore[arg-type]
        )
        QGuiApplication.clipboard().setText(self._status_message())
        if not open_web_url(url):
            QMessageBox.warning(
                self,
                "MarkusDispatcher",
                _tr("Nie udało się otworzyć przeglądarki z wiadomością."),
            )

    def _reload_history(self, *, select_job_id: int | None = None) -> None:
        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        self.history_combo.addItem("— nowe zlecenie —", None)
        if self._driver_id is not None:
            jobs = monthly_settlements.jobs_for_month(self._driver_id, self.selected_month())
            known = {job.id for job in jobs}
            jobs += [job for job in db.list_jobs_for_driver(self._driver_id)
                     if job.id not in known and job.local_only and not job.unload_datetime]
            for job in jobs:
                when = job.unload_datetime or "?"
                ref = job.ref_numbers or f"#{job.id}"
                label = f"{when} · {ref}"
                if job.local_only and not all((job.ref_numbers, job.client_name, job.load_address,
                                               job.unload_address, job.load_datetime, job.unload_datetime,
                                               job.pod_requirements)):
                    label = '⚠ ' + label
                self.history_combo.addItem(label, job.id)
        if select_job_id is not None:
            idx = self.history_combo.findData(select_job_id)
            if idx >= 0:
                self.history_combo.setCurrentIndex(idx)
        self.history_combo.blockSignals(False)

    def _on_history_picked(self, index: int) -> None:
        if self._loading_history:
            return
        job_id = self.history_combo.itemData(index)
        if job_id is None:
            self.new_job(driver_id=self._driver_id)
            return
        job = db.get_job(int(job_id))
        if job is not None:
            self.load_job(job)

    def _ensure_saved_for_docs(self) -> JobData | None:
        if self._driver_id is None:
            QMessageBox.warning(
                self,
                "MarkusDispatcher",
                _tr("Najpierw wybierz kierowcę — dokumenty trafiają do jego folderu."),
            )
            return None
        job = self.collect()
        if job.client_name and not job.local_only:
            client = db.find_or_create_client_by_name(job.client_name)
            if client is not None:
                job.client_id = client.id
                self._client_id = client.id
        saved = db.save_job(job)
        self.settlement.save_draft(saved.id)
        saved = db.sync_job_to_archive(
            saved, instruction_footer=self._footer_text
        )
        self._job_id = saved.id
        self._archive_path = saved.archive_path
        self._archive_import_documents(saved)
        self._reload_history(select_job_id=saved.id)
        return saved

    def _docs_dir(self) -> Path | None:
        if not self._archive_path:
            return None
        return ARCHIVE_ROOT / self._archive_path / "dokumenty"

    def _reload_attachments(self) -> None:
        self.docs_list.clear()
        if self._job_id is None:
            self.archive_hint.setText(
                _tr("Zapisz zlecenie z wybranym kierowcą, aby utworzyć folder ewidencji.")
            )
            return
        for att in db.list_attachments(self._job_id):
            kind_pl = {
                "pdf": "PDF",
                "excel": "Excel",
                "sheets": "Sheets",
                "pod": "POD",
                "file": _tr("Plik"),
            }.get(att.kind, att.kind)
            label = f"[{kind_pl}] {att.stored_name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, att.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, att.stored_name)
            item.setData(Qt.ItemDataRole.UserRole + 2, att.source_url)
            self.docs_list.addItem(item)
        if self._archive_path:
            self.archive_hint.setText(
                f"{_tr('Folder: ')}{ARCHIVE_ROOT / self._archive_path}"
            )
        else:
            self.archive_hint.setText("")

    def _add_file_attachment(self) -> None:
        saved = self._ensure_saved_for_docs()
        if saved is None:
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            _tr("Wybierz PDF lub Excel"),
            "",
            _tr("Dokumenty (*.pdf *.xlsx *.xls *.xlsm *.csv);;Wszystkie (*.*)"),
        )
        if not path_str:
            return
        source = Path(path_str)
        docs = self._docs_dir()
        assert docs is not None
        try:
            dest = copy_file_attachment(docs, source)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "MarkusDispatcher", str(exc))
            return
        kind = detect_kind(dest)
        db.add_attachment(
            saved.id,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            original_name=source.name,
            stored_name=dest.name,
        )
        self._reload_attachments()
        self.saved.emit()

    def _add_sheets_link(self) -> None:
        saved = self._ensure_saved_for_docs()
        if saved is None:
            return
        url, ok = QInputDialog.getText(
            self,
            "Google Sheets",
            _tr("Wklej link do arkusza Google:"),
        )
        if not ok or not url.strip():
            return
        docs = self._docs_dir()
        assert docs is not None
        try:
            dest = write_sheets_shortcut(docs, url.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "MarkusDispatcher", str(exc))
            return
        db.add_attachment(
            saved.id,  # type: ignore[arg-type]
            kind="sheets",
            original_name="Google Sheets",
            stored_name=dest.name,
            source_url=url.strip(),
        )
        self._reload_attachments()
        self.saved.emit()

    def _register_pod_pdf(self, job_id: int, dest: Path, original_name: str) -> None:
        db.add_attachment(
            job_id,
            kind="pod",
            original_name=original_name,
            stored_name=dest.name,
        )

    def _add_pod_files(self) -> None:
        saved = self._ensure_saved_for_docs()
        if saved is None or saved.id is None:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            _tr("Wybierz zdjęcia POD (każde → osobny PDF)"),
            "",
            _tr("Zdjęcia (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;Wszystkie (*.*)"),
        )
        if not paths:
            return
        docs = self._docs_dir()
        assert docs is not None
        ok = 0
        errors: list[str] = []
        for path_str in paths:
            source = Path(path_str)
            try:
                dest = save_image_file_as_pod_pdf(docs, source)
                self._register_pod_pdf(saved.id, dest, source.name)
                ok += 1
            except (OSError, ValueError) as exc:
                errors.append(f"{source.name}: {exc}")
        self._reload_attachments()
        self.saved.emit()
        if errors:
            QMessageBox.warning(
                self,
                "MarkusDispatcher",
                f"{_tr('Zapisano POD: ')}{ok}{_tr('.\nBłędy:\n')}" + "\n".join(errors[:5]),
            )
        elif ok:
            QMessageBox.information(
                self,
                "MarkusDispatcher",
                f"{_tr('Zapisano ')}{ok} POD jako osobne PDF w folderze zlecenia.",
            )

    def _paste_pod_from_clipboard(self) -> bool:
        """Wkleja obraz ze schowka jako osobny PDF POD. Zwraca True jeśli OK."""
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        image: QImage | None = None
        if mime is not None and mime.hasImage():
            raw = clipboard.image()
            if not raw.isNull():
                image = raw
        if image is None or image.isNull():
            QMessageBox.information(
                self,
                "MarkusDispatcher",
                _tr("W schowku nie ma zdjęcia. Skopiuj fotkę (np. z WhatsApp) i spróbuj ponownie."),
            )
            return False
        saved = self._ensure_saved_for_docs()
        if saved is None or saved.id is None:
            return False
        docs = self._docs_dir()
        assert docs is not None
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            QMessageBox.warning(
                self, "MarkusDispatcher", _tr("Nie udało się odczytać obrazu ze schowka.")
            )
            return False
        try:
            dest = save_image_bytes_as_pod_pdf(
                docs, bytes(buffer.data()), label="pod"
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "MarkusDispatcher", str(exc))
            return False
        self._register_pod_pdf(saved.id, dest, "clipboard.png")
        self._reload_attachments()
        self.saved.emit()
        QMessageBox.information(
            self,
            "MarkusDispatcher",
            f"POD zapisany jako PDF:\n{dest.name}",
        )
        return True

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            if self._paste_pod_from_clipboard():
                event.accept()
                return
        super().keyPressEvent(event)

    def _open_archive_folder(self) -> None:
        if not self._archive_path:
            QMessageBox.information(
                self,
                "MarkusDispatcher",
                _tr("Brak folderu — najpierw zapisz zlecenie z kierowcą."),
            )
            return
        folder = ARCHIVE_ROOT / self._archive_path
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _open_selected_doc(self) -> None:
        item = self.docs_list.currentItem()
        if item is None:
            return
        stored = item.data(Qt.ItemDataRole.UserRole + 1)
        source_url = item.data(Qt.ItemDataRole.UserRole + 2) or ""
        if source_url:
            QDesktopServices.openUrl(QUrl(str(source_url)))
            return
        docs = self._docs_dir()
        if docs is None or not stored:
            return
        path = docs / str(stored)
        if not path.exists():
            QMessageBox.warning(self, "MarkusDispatcher", f"{_tr('Brak pliku: ')}{path}")
            return
        if path.suffix.lower() == ".url":
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if line.upper().startswith("URL="):
                    QDesktopServices.openUrl(QUrl(line.split("=", 1)[1].strip()))
                    return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
