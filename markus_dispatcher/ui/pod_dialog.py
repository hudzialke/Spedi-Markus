"""Explicit local POD review, package creation and manual sent status."""
from datetime import datetime, timedelta, timezone
import re

from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from spedi.background import run_background
from spedi.i18n import tr
from spedi.weekly_watch import WARSAW


def local_deadline(value, fold=None):
    choices = {}
    for occurrence in (0, 1):
        instant = value.replace(tzinfo=WARSAW, fold=occurrence).astimezone(timezone.utc)
        if instant.astimezone(WARSAW).replace(tzinfo=None) == value:
            choices[occurrence] = instant
    if not choices:
        raise ValueError('Ta godzina nie istnieje przy zmianie czasu.')
    if len(set(choices.values())) > 1 and fold not in choices:
        raise ValueError('Godzina występuje dwukrotnie. Wybierz pierwsze albo drugie wystąpienie.')
    return choices[fold] if fold in choices else next(iter(choices.values()))


class PodDialog(QDialog):
    def __init__(self, store, job_id, parent=None):
        super().__init__(parent)
        self.store, self.job_id = store, job_id
        with store._connect(readonly=True) as conn:
            job = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            attachments = [dict(row) for row in conn.execute('SELECT * FROM attachments WHERE job_id=? ORDER BY id', (job_id,))]
        if job is None:
            raise ValueError('Zlecenie nie istnieje.')
        self.job = dict(job)
        state = store.get(job_id)
        self.setWindowTitle(tr('Dokumenty i termin POD')+' · '+(job['ref_numbers'] or f'#{job_id}'))
        self.resize(880, 670)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('Warunki zapisane przy zleceniu')))
        requirements = QPlainTextEdit(job['pod_requirements'])
        requirements.setReadOnly(True)
        requirements.setMaximumHeight(110)
        layout.addWidget(requirements)
        form = QFormLayout()
        self.required = QLineEdit(', '.join(state['required_docs']) if state else 'CMR')
        form.addRow(tr('Wymagane dokumenty (oddziel przecinkami)'), self.required)
        self.has_due = QCheckBox(tr('Pilnuj zatwierdzonego terminu POD'))
        self.has_due.setChecked(bool(state and state['deadline_utc']))
        form.addRow(self.has_due)
        due = (datetime.fromisoformat(state['deadline_utc']).astimezone(WARSAW)
               if state and state['deadline_utc'] else datetime.now(WARSAW)+timedelta(days=1))
        date_row = QHBoxLayout()
        self.day = QDateEdit(QDate(due.year, due.month, due.day))
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat('dd.MM.yyyy')
        self.clock = QTimeEdit(QTime(due.hour, due.minute))
        self.clock.setDisplayFormat('HH:mm')
        self.fold = QComboBox()
        for text, value in (('Godzina jednoznaczna', None), ('Pierwsze wystąpienie', 0), ('Drugie wystąpienie', 1)):
            self.fold.addItem(tr(text), value)
        if state and state['deadline_utc']:
            self.fold.setCurrentIndex(self.fold.findData(due.fold))
        for widget in (self.day, self.clock, self.fold):
            date_row.addWidget(widget)
            widget.setEnabled(self.has_due.isChecked())
            self.has_due.toggled.connect(widget.setEnabled)
        form.addRow(tr('Termin · Europe/Warsaw'), date_row)
        layout.addLayout(form)
        layout.addWidget(QLabel(tr('Zaznacz dokumenty do paczki i przypisz typy, np. CMR, WZ. Jeden plik może zawierać kilka typów.')))
        self.documents = QTableWidget(len(attachments), 3)
        self.documents.setHorizontalHeaderLabels([tr('Do paczki'), tr('Dokument'), tr('Typy dokumentów')])
        self.documents.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        selected = {item['id']: item['doc_types'] for item in state['documents']} if state else {}
        for row, attachment in enumerate(attachments):
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checked.setCheckState(Qt.CheckState.Checked if attachment['id'] in selected else Qt.CheckState.Unchecked)
            checked.setData(Qt.ItemDataRole.UserRole, attachment['id'])
            self.documents.setItem(row, 0, checked)
            self.documents.setItem(row, 1, QTableWidgetItem(attachment['original_name']))
            types = QLineEdit(', '.join(selected.get(attachment['id'], [])))
            types.setPlaceholderText('CMR, WZ')
            self.documents.setCellWidget(row, 2, types)
        self.documents.setColumnWidth(0, 90)
        self.documents.setColumnWidth(1, 390)
        self.documents.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.documents, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        save = buttons.addButton(tr('Zapisz ustawienia'), QDialogButtonBox.ButtonRole.ActionRole)
        save.clicked.connect(self._save)
        package = buttons.addButton(tr('Zapisz paczkę PDF / ZIP…'), QDialogButtonBox.ButtonRole.ActionRole)
        package.clicked.connect(self._package)
        sent = buttons.addButton(tr('Oznacz jako wysłane…'), QDialogButtonBox.ButtonRole.ActionRole)
        sent.clicked.connect(self._mark_sent)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_status()

    def _refresh_status(self):
        state = self.store.get(self.job_id)
        if not state:
            text = tr('Ustaw wymagania dokumentów i zatwierdź termin wynikający ze zlecenia.')
        elif state['sent_at']:
            stamp = datetime.fromisoformat(state['sent_at']).astimezone(WARSAW).strftime('%d.%m.%Y %H:%M')
            text = tr('Oznaczono jako wysłane: ')+stamp
        elif state['needs_review']:
            text = tr('Termin rozładunku zmienił się. Sprawdź i ponownie zatwierdź termin POD.')
        elif state['missing_docs']:
            text = tr('Brakuje dokumentów: ')+', '.join(state['missing_docs'])
        else:
            text = tr('Komplet dokumentów jest gotowy do zapisania w paczce.')
        self.status.setText(text)

    def _save(self):
        try:
            date, clock = self.day.date(), self.clock.time()
            due = (local_deadline(datetime(date.year(), date.month(), date.day(), clock.hour(), clock.minute()), self.fold.currentData())
                   if self.has_due.isChecked() else None)
            documents = {self.documents.item(row, 0).data(Qt.ItemDataRole.UserRole): self.documents.cellWidget(row, 2).text()
                         for row in range(self.documents.rowCount())
                         if self.documents.item(row, 0).checkState() == Qt.CheckState.Checked}
            self.store.save(self.job_id, deadline=due, required_docs=self.required.text(), documents=documents)
            self._refresh_status()
            return True
        except Exception as exc:
            QMessageBox.warning(self, tr('Nie zapisano ustawień POD'), str(exc))
            return False

    def _package(self):
        if not self._save():
            return
        name = re.sub(r'[^A-Za-z0-9._-]', '_', self.job['ref_numbers'] or str(self.job_id))
        path, _ = QFileDialog.getSaveFileName(self, tr('Zapisz paczkę POD'), 'POD-'+name+'.pdf', 'PDF / ZIP (*.pdf *.zip)')
        if path:
            try:
                run_background(self, tr('Przygotowywanie paczki POD…'), lambda task: self.store.package(self.job_id, path))
            except Exception as exc:
                QMessageBox.warning(self, tr('Nie przygotowano paczki POD'), str(exc))

    def _mark_sent(self):
        if not self._save():
            return
        if QMessageBox.question(self, tr('Status dokumentów POD'), tr('Oznaczyć wybrane dokumenty jako wysłane?'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                self.store.mark_sent(self.job_id)
                self._refresh_status()
            except Exception as exc:
                QMessageBox.warning(self, tr('Nie zmieniono statusu POD'), str(exc))
