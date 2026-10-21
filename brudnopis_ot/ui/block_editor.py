from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QTime, QTimeZone
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from brudnopis_ot.core.calendar_weeks import format_duration_hm
from brudnopis_ot.core.display_tz import (
    DEFAULT_ZONE,
    DisplayZone,
    local_time_candidates,
    utc_naive_to_zone,
    zone_naive_to_utc,
)
from brudnopis_ot.core.models import ACTIVITY_LABELS_PL, ActivityKind, Block


class BlockEditorDialog(QDialog):
    """Add / edit a single activity block."""

    def __init__(
        self,
        parent=None,
        *,
        block: Block | None = None,
        default_start: datetime | None = None,
        zone: DisplayZone = DEFAULT_ZONE,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Edycja bloku") if block else _tr("Nowy blok"))
        self._block_id = block.id if block else None
        self._zone = zone
        self._original_start_utc = block.start if block else None
        self._original_end_utc = block.end if block else None
        self._start_changed = False
        self._end_changed = False
        self._resolved_start_utc: datetime | None = None
        self._resolved_end_utc: datetime | None = None

        self.kind = QComboBox()
        for kind in ActivityKind:
            self.kind.addItem(ACTIVITY_LABELS_PL[kind], kind)

        self.start = QDateTimeEdit()
        self.start.setTimeZone(QTimeZone.utc())  # neutral wall-clock editor; conversion is explicit below
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.end = QDateTimeEdit()
        self.end.setTimeZone(QTimeZone.utc())
        self.end.setCalendarPopup(True)
        self.end.setDisplayFormat("dd.MM.yyyy HH:mm")

        self.duration_display = QLabel("—")
        font = QFont()
        font.setBold(True)
        font.setPointSize(11)
        self.duration_display.setFont(font)

        self.duration_hours = QLineEdit()
        self.duration_hours.setPlaceholderText(_tr("np. 24 lub 45"))
        apply_dur = QPushButton(_tr("Ustaw koniec z długości"))
        apply_dur.clicked.connect(self._apply_duration)

        self.country = QLineEdit()
        self.country.setPlaceholderText("PL / SE / D …")
        self.abroad = QCheckBox(_tr("Rozpoczęty poza państwem siedziby (PM)"))
        self.label = QLineEdit()

        if block is not None:
            idx = self.kind.findData(block.kind)
            if idx >= 0:
                self.kind.setCurrentIndex(idx)
            self.start.setDateTime(_to_qdt(utc_naive_to_zone(block.start, zone)))
            self.end.setDateTime(_to_qdt(utc_naive_to_zone(block.end, zone)))
            if block.country_start:
                self.country.setText(block.country_start)
            self.abroad.setChecked(block.started_abroad)
            if block.label:
                self.label.setText(block.label)
        else:
            if default_start is not None:
                start = utc_naive_to_zone(default_start, zone)
            else:
                start = datetime.now().replace(second=0, microsecond=0)
            self.start.setDateTime(_to_qdt(start))
            self.end.setDateTime(_to_qdt(start + timedelta(hours=9)))
            self.kind.setCurrentIndex(self.kind.findData(ActivityKind.DRIVE))

        self.start.dateTimeChanged.connect(self._start_was_changed)
        self.end.dateTimeChanged.connect(self._end_was_changed)

        form = QFormLayout()
        form.addRow(_tr("Rodzaj"), self.kind)
        form.addRow("Start", self.start)
        form.addRow(_tr("Koniec"), self.end)
        form.addRow(_tr("Czas trwania"), self.duration_display)
        dur_row = QHBoxLayout()
        dur_row.addWidget(self.duration_hours)
        dur_row.addWidget(apply_dur)
        form.addRow(_tr("Ustaw długość (h)"), dur_row)
        form.addRow(_tr("Kraj startu"), self.country)
        form.addRow("", self.abroad)
        form.addRow(_tr("Etykieta"), self.label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        self.delete_btn = QPushButton(_tr("Usuń blok"))
        self.delete_btn.setVisible(block is not None)
        self._delete_requested = False
        self.delete_btn.clicked.connect(self._request_delete)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            QLabel(_tr("Dla OT tygodniowego wybierz „Postój / odpoczynek” i ustaw 24h lub 45h+."))
        )
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch()
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

        self._update_duration_display()

    def _update_duration_display(self, *_args) -> None:
        start = _from_qdt(self.start.dateTime())
        end = _from_qdt(self.end.dateTime())
        start_candidates = local_time_candidates(start, self._zone)
        end_candidates = local_time_candidates(end, self._zone)
        if not start_candidates or not end_candidates:
            self.duration_display.setText(_tr("— (nieistniejąca godzina przy zmianie czasu)"))
            self.duration_display.setStyleSheet("color: #e11d48;")
            return
        if len(start_candidates) > 1 or len(end_candidates) > 1:
            self.duration_display.setText("— (wariant UTC wybierzesz przy zapisie)")
            self.duration_display.setStyleSheet("color: #9a6700;")
            return
        if end <= start:
            self.duration_display.setText(_tr("— (koniec musi być po starcie)"))
            self.duration_display.setStyleSheet("color: #e11d48;")
            return
        delta = end - start
        total_minutes = int(delta.total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        hm = format_duration_hm(total_minutes / 60.0)
        self.duration_display.setText(f"{hm}  ({hours} h {minutes:02d} min)")
        self.duration_display.setStyleSheet("color: #101820;")

    def _start_was_changed(self, *_args) -> None:
        self._start_changed = True
        self._resolved_start_utc = None
        self._update_duration_display()

    def _end_was_changed(self, *_args) -> None:
        self._end_changed = True
        self._resolved_end_utc = None
        self._update_duration_display()

    def _resolve_wall_time(
        self, label: str, value: datetime, original_utc: datetime | None, changed: bool
    ) -> datetime | None:
        candidates = local_time_candidates(value, self._zone)
        if not candidates:
            QMessageBox.warning(
                self,
                _tr("Nieistniejąca godzina"),
                f"{label}: {value:%d.%m.%Y %H:%M}{_tr(' nie istnieje w czasie polskim, ponieważ zegar jest przestawiany na czas letni. Wybierz inną godzinę.')}",
            )
            return None
        if len(candidates) == 1:
            return candidates[0].utc
        if original_utc is not None and not changed:
            original = original_utc.replace(tzinfo=None)
            matched = next((item for item in candidates if item.utc == original), None)
            if matched is not None:
                return matched.utc
        descriptions = [
            f"{'pierwsze' if index == 0 else 'drugie'} {value:%H:%M} "
            f"({item.offset_label}) → {item.utc:%H:%M} UTC"
            for index, item in enumerate(candidates)
        ]
        selected, ok = QInputDialog.getItem(
            self,
            _tr("Godzina występuje dwa razy"),
            f"{label} {value:%d.%m.%Y %H:%M}{_tr(' występuje dwa razy. Wybierz właściwy wariant:')}",
            descriptions,
            0,
            False,
        )
        if not ok:
            return None
        return candidates[descriptions.index(selected)].utc

    def _apply_duration(self) -> None:
        text = self.duration_hours.text().strip().replace(",", ".")
        try:
            hours = float(text)
        except ValueError:
            QMessageBox.warning(self, _tr("Błąd"), _tr("Podaj liczbę godzin."))
            return
        if hours <= 0:
            QMessageBox.warning(self, _tr("Błąd"), _tr("Długość musi być > 0."))
            return
        start_local = _from_qdt(self.start.dateTime())
        start_utc = self._resolve_wall_time(
            "Start", start_local, self._original_start_utc, self._start_changed
        )
        if start_utc is None:
            return
        end_utc = start_utc + timedelta(hours=hours)
        end_local = utc_naive_to_zone(end_utc, self._zone)
        self.end.setDateTime(_to_qdt(end_local))
        # Preserve the exact chosen occurrence when _accept validates the fields.
        self._original_start_utc = start_utc
        self._original_end_utc = end_utc
        self._start_changed = False
        self._end_changed = False
        self._resolved_start_utc = start_utc
        self._resolved_end_utc = end_utc
        self._update_duration_display()

    def _request_delete(self) -> None:
        self._delete_requested = True
        self.accept()

    def _accept(self) -> None:
        start = _from_qdt(self.start.dateTime())
        end = _from_qdt(self.end.dateTime())
        resolved_start = self._resolve_wall_time(
            "Start", start, self._original_start_utc, self._start_changed
        )
        if resolved_start is None:
            return
        resolved_end = self._resolve_wall_time(
            _tr("Koniec"), end, self._original_end_utc, self._end_changed
        )
        if resolved_end is None:
            return
        if resolved_end <= resolved_start:
            QMessageBox.warning(self, _tr("Błąd"), _tr("Koniec musi być po starcie."))
            return
        self._resolved_start_utc = resolved_start
        self._resolved_end_utc = resolved_end
        self.accept()

    @property
    def delete_requested(self) -> bool:
        return self._delete_requested

    def result_block(self) -> Block | None:
        if self._delete_requested:
            return None
        start = self._resolved_start_utc or zone_naive_to_utc(
            _from_qdt(self.start.dateTime()), self._zone
        )
        end = self._resolved_end_utc or zone_naive_to_utc(
            _from_qdt(self.end.dateTime()), self._zone
        )
        # Qt's QVariant conversion may return str for a str-based Enum.
        kind = ActivityKind(self.kind.currentData())
        country = self.country.text().strip() or None
        kwargs = {
            "kind": kind,
            "start": start,
            "end": end,
            "country_start": country,
            "started_abroad": self.abroad.isChecked(),
            "label": self.label.text().strip() or None,
        }
        if self._block_id:
            kwargs["id"] = self._block_id
        return Block(**kwargs)


def _to_qdt(dt: datetime):
    from PySide6.QtCore import QDateTime

    qdt = QDateTime(
        QDate(dt.year, dt.month, dt.day),
        QTime(dt.hour, dt.minute, dt.second),
        QTimeZone.utc(),
    )
    return qdt


def _from_qdt(qdt) -> datetime:
    # PySide6: toPython() → datetime; fallback via components
    if hasattr(qdt, "toPython"):
        value = qdt.toPython()
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
    d = qdt.date()
    t = qdt.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second())
