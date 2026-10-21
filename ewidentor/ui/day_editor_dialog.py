from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ewidentor.core.brudnopis_import import RawSegment
from ewidentor.core.day_overrides import (
    ACTIVITY_KEYS,
    ACTIVITY_LABELS_PL,
    detect_gaps,
    format_day_summary,
    normalize_activity,
    override_dicts_from_segments,
    parse_hm,
    validate_blocks,
)
from ewidentor.core.display_tz import DEFAULT_ZONE, DisplayZone, local_time_candidates, zone_label
from ewidentor.core.month_card import format_clock_local


class DayEditorDialog(QDialog):
    """Edycja bloków jednego dnia (minuty UTC karty, wyświetlanie w strefie UI)."""

    RESULT_SAVE = 1
    RESULT_RESTORE = 2

    def __init__(
        self,
        parent=None,
        *,
        day: date,
        segments: list[RawSegment],
        zone: DisplayZone = DEFAULT_ZONE,
        has_override: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{_tr('Edytuj dzień — ')}{day.strftime('%d.%m.%Y')}")
        self.resize(640, 480)
        self._day = day
        self._zone = zone
        self._action = 0

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        zone_note = zone_label(zone)
        self.hint = QLabel(
            f"{_tr('Bloki aktywności · wyświetlanie: ')}{zone_note}. Godziny zapisujemy jak w karcie tachografu (UTC dnia)."
            + (_tr(" · ten dzień ma ręczne nadpisanie.") if has_override else "")
        )
        self.hint.setWordWrap(True)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([_tr("Od"), _tr("Do"), _tr("Aktywność")])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)

        for seg in sorted(segments, key=lambda s: (s.start_minute, s.end_minute)):
            self._append_row(seg.start_minute, seg.end_minute, normalize_activity(seg.activity))

        btns_row = QHBoxLayout()
        b_add = QPushButton(_tr("Dodaj blok"))
        b_add.clicked.connect(self._add_block)
        b_del = QPushButton(_tr("Usuń blok"))
        b_del.clicked.connect(self._delete_block)
        self._fill_gap_btn = QPushButton(_tr("Dodaj blok na lukę"))
        self._fill_gap_btn.clicked.connect(self._add_block_for_gap)
        btns_row.addWidget(b_add)
        btns_row.addWidget(b_del)
        btns_row.addWidget(self._fill_gap_btn)
        btns_row.addStretch()

        buttons = QDialogButtonBox()
        self._save_btn = buttons.addButton(
            _tr("Zapisz"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._restore_btn = buttons.addButton(
            _tr("Przywróć z Brudnopisu"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self._cancel_btn = buttons.addButton(
            _tr("Anuluj"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self._save_btn.clicked.connect(self._on_save)
        self._restore_btn.clicked.connect(self._on_restore)
        self._cancel_btn.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.summary)
        layout.addLayout(btns_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

        self.table.itemChanged.connect(lambda *_: self._refresh_summary())
        self._refresh_summary()

    def action(self) -> int:
        return self._action

    def result_segments(self) -> list[RawSegment]:
        return self._read_segments(strict=False)

    def result_override_dicts(self) -> list[dict]:
        return override_dicts_from_segments(self.result_segments())

    def _append_row(self, start_min: int, end_min: int, activity: str) -> None:
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)
        start_item = QTableWidgetItem(format_clock_local(self._day, start_min, self._zone))
        end_item = QTableWidgetItem(format_clock_local(self._day, end_min, self._zone))
        start_item.setData(Qt.ItemDataRole.UserRole, int(start_min))
        end_item.setData(Qt.ItemDataRole.UserRole, int(end_min))
        self.table.setItem(row, 0, start_item)
        self.table.setItem(row, 1, end_item)
        combo = QComboBox()
        for key in ACTIVITY_KEYS:
            combo.addItem(ACTIVITY_LABELS_PL[key], key)
        idx = combo.findData(activity if activity in ACTIVITY_KEYS else "rest")
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.currentIndexChanged.connect(lambda *_: self._refresh_summary())
        self.table.setCellWidget(row, 2, combo)
        self.table.blockSignals(False)

    def _add_block(self) -> None:
        segs = self._read_segments(strict=False)
        start = max((s.end_minute for s in segs), default=0)
        end = min(1440, start + 60)
        if end <= start:
            start, end = 0, 60
        self._append_row(start, end, "work")
        self._refresh_summary()

    def _add_block_for_gap(self) -> None:
        segs = self._read_segments(strict=False)
        gaps = detect_gaps(segs)
        if not gaps:
            QMessageBox.information(
                self,
                _tr("Edytuj dzień"),
                _tr("Brak luk między blokami do uzupełnienia."),
            )
            return
        start, end = gaps[0]
        self._append_row(start, end, "rest")
        self._refresh_summary()

    def _delete_block(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        self.table.removeRow(row)
        self._refresh_summary()

    def _read_segments(self, *, strict: bool) -> list[RawSegment]:
        out: list[RawSegment] = []
        for row in range(self.table.rowCount()):
            start_item = self.table.item(row, 0)
            end_item = self.table.item(row, 1)
            combo = self.table.cellWidget(row, 2)
            if start_item is None or end_item is None or combo is None:
                if strict:
                    raise ValueError(f"{_tr('Pusty wiersz ')}{row + 1}.")
                continue
            start = self._minute_from_item(start_item, row, _tr("Od"), strict)
            end = self._minute_from_item(end_item, row, _tr("Do"), strict)
            if start is None or end is None:
                if strict:
                    raise ValueError(f"{_tr('Niepoprawna godzina w wierszu ')}{row + 1} (HH:MM).")
                continue
            start = max(0, min(1440, start))
            end = max(0, min(1440, end))
            activity = normalize_activity(str(combo.currentData() or "rest"))
            if end <= start:
                if strict:
                    raise ValueError(f"{_tr('Koniec musi być po starcie (wiersz ')}{row + 1}).")
                continue
            out.append(
                RawSegment(
                    day=self._day,
                    activity=activity,
                    start_minute=start,
                    end_minute=end,
                )
            )
        out.sort(key=lambda s: (s.start_minute, s.end_minute))
        return out

    def _minute_from_item(
        self, item: QTableWidgetItem, row: int, label: str, strict: bool
    ) -> int | None:
        parsed = parse_hm(item.text())
        if parsed is None:
            return None
        h, minute = parsed
        from datetime import datetime, time, timedelta

        local_dt = (
            datetime.combine(self._day, time.min) + timedelta(days=1)
            if (h, minute) == (24, 0)
            else datetime.combine(self._day, time(h, minute))
        )
        candidates = local_time_candidates(local_dt, self._zone)
        if not candidates:
            if strict:
                raise ValueError(
                    f"{_tr('Wiersz ')}{row + 1}, {label}: godzina {item.text()}{_tr(' nie istnieje w czasie polskim z powodu przejścia na czas letni.')}"
                )
            return None
        base = datetime.combine(self._day, time.min)
        values = [int(round((candidate.utc - base).total_seconds() / 60)) for candidate in candidates]
        if len(candidates) == 1:
            return values[0]
        original = item.data(Qt.ItemDataRole.UserRole)
        if original is not None:
            original = int(original)
            if original in values and item.text().strip() == format_clock_local(
                self._day, original, self._zone
            ):
                return original
        if not strict:
            return None
        descriptions = [
            f"{'pierwsze' if index == 0 else 'drugie'} {item.text().strip()} "
            f"({candidate.offset_label}) → {candidate.utc:%H:%M} UTC"
            for index, candidate in enumerate(candidates)
        ]
        selected, ok = QInputDialog.getItem(
            self,
            _tr("Godzina występuje dwa razy"),
            f"{_tr('Wiersz ')}{row + 1}, {label}{_tr(': wybierz właściwe wystąpienie godziny:')}",
            descriptions,
            0,
            False,
        )
        if not ok:
            raise ValueError(_tr("Anulowano wybór wariantu czasu. Dane nie zostały zapisane."))
        value = values[descriptions.index(selected)]
        item.setData(Qt.ItemDataRole.UserRole, value)
        return value

    def _refresh_summary(self) -> None:
        segs = self._read_segments(strict=False)
        text = format_day_summary(segs)
        gaps = detect_gaps(segs)
        self._fill_gap_btn.setEnabled(bool(gaps))
        if gaps:
            listed = ", ".join(
                f"{format_clock_local(self._day, a, self._zone)}"
                f"–{format_clock_local(self._day, b, self._zone)}"
                for a, b in gaps
            )
            text += (
                f"{_tr('\nUwaga: luki między blokami (')}{listed}{_tr(') — uzupełnij ręcznie przed zapisem.')}"
            )
        self.summary.setText(text)

    def _on_save(self) -> None:
        try:
            segs = self._read_segments(strict=True)
        except ValueError as exc:
            QMessageBox.warning(self, _tr("Edytuj dzień"), str(exc))
            return
        err = validate_blocks(segs)
        if err:
            QMessageBox.warning(self, _tr("Edytuj dzień"), err)
            return
        self._action = self.RESULT_SAVE
        self.accept()

    def _on_restore(self) -> None:
        if (
            QMessageBox.question(
                self,
                _tr("Przywróć z Brudnopisu"),
                _tr("Usunąć ręczne nadpisanie tego dnia i wrócić do danych z Brudnopisu/DDD?"),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._action = self.RESULT_RESTORE
        self.accept()
