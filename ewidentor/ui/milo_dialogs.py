from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ewidentor.core.milo import MiloStay
from ewidentor.core.milo_rates import (
    MILO_OP_GLOSSARY_PL,
    OP_LABELS_PL,
    CountryRate,
    OpType,
    coerce_op_type,
)
from ewidentor.core.milo_suggest import MiloOpSuggestion, format_suggestion_hint, mark_note_manual
from ewidentor.core.store import EwidentorStore
from ewidentor.ui import theme as T


def _glossary_label() -> QLabel:
    lab = QLabel(MILO_OP_GLOSSARY_PL)
    lab.setObjectName("sectionHint")
    lab.setWordWrap(True)
    return lab


class MiloStayDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        store: EwidentorStore,
        default_day: date | None = None,
        stay: MiloStay | None = None,
        suggestion: MiloOpSuggestion | None = None,
        dispatcher_hint: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Wpis MiLo (kraj / typ operacji)"))
        self._stay = stay
        self._orig_op = stay.op_type if stay else None
        self._suggestion = suggestion
        rates = [r for r in store.list_country_rates() if r.enabled]
        if not rates:
            rates = store.list_country_rates()

        self.day_edit = QDateEdit()
        self.day_edit.setCalendarPopup(True)
        self.day_edit.setDisplayFormat("yyyy-MM-dd")
        day = stay.day if stay else (default_day or date.today())
        self.day_edit.setDate(day)

        self.country_combo = QComboBox()
        for r in rates:
            self.country_combo.addItem(
                f"{r.country_code} — {r.name_pl} ({r.hourly_rate:g} {r.currency}/h)",
                r.country_code,
            )
        if stay:
            idx = self.country_combo.findData(stay.country_code.upper())
            if idx >= 0:
                self.country_combo.setCurrentIndex(idx)

        self.op_combo = QComboBox()
        for op, label in OP_LABELS_PL.items():
            # Store .value — QComboBox returns plain str for enum userData.
            self.op_combo.addItem(label, op.value)
        if stay:
            idx = self.op_combo.findData(coerce_op_type(stay.op_type).value)
            if idx >= 0:
                self.op_combo.setCurrentIndex(idx)

        self.hours_spin = QSpinBox()
        self.hours_spin.setRange(0, 24)
        self.mins_spin = QSpinBox()
        self.mins_spin.setRange(0, 59)
        total = stay.minutes if stay else 0
        self.hours_spin.setValue(total // 60)
        self.mins_spin.setValue(total % 60)

        time_row = QHBoxLayout()
        time_row.addWidget(self.hours_spin)
        time_row.addWidget(QLabel("h"))
        time_row.addWidget(self.mins_spin)
        time_row.addWidget(QLabel(_tr("min (jazda+praca+dyżur)")))

        form = QFormLayout()
        form.addRow(_tr("Dzień"), self.day_edit)
        form.addRow(_tr("Kraj"), self.country_combo)
        form.addRow(_tr("Typ operacji"), self.op_combo)
        form.addRow(_tr("Czas MiLo"), time_row)

        self.suggest_label = QLabel()
        self.suggest_label.setObjectName("miloSuggest")
        self.suggest_label.setWordWrap(True)
        self.suggest_label.setStyleSheet(
            f"color: {T.SUGGEST}; font-weight: 600; font-size: 13px;"
        )
        self._apply_hint_btn = QPushButton(_tr("Ustaw jak sugestia"))
        self._apply_hint_btn.clicked.connect(self._apply_suggestion)
        hint_row = QHBoxLayout()
        hint_row.addWidget(self.suggest_label, 1)
        hint_row.addWidget(self._apply_hint_btn)
        form.addRow("", hint_row)

        self.disp_label = QLabel()
        self.disp_label.setObjectName("miloSuggest")
        self.disp_label.setWordWrap(True)
        self.disp_label.setStyleSheet(
            f"color: {T.SUGGEST}; font-weight: 600; font-size: 13px;"
        )
        form.addRow("", self.disp_label)
        self._set_hints(suggestion, dispatcher_hint)

        self.glossary_label = _glossary_label()
        form.addRow(self.glossary_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _set_hints(
        self, suggestion: MiloOpSuggestion | None, dispatcher_hint: str
    ) -> None:
        if suggestion is None:
            self.suggest_label.setText(_tr("wg DDD: brak innej sugestii niż obecny typ"))
            self._apply_hint_btn.setEnabled(False)
        else:
            self.suggest_label.setText(format_suggestion_hint(suggestion))
            self._apply_hint_btn.setEnabled(True)
        text = (dispatcher_hint or "").strip()
        self.disp_label.setText(text)
        self.disp_label.setVisible(bool(text))

    def _apply_suggestion(self) -> None:
        if self._suggestion is None:
            return
        idx = self.op_combo.findData(self._suggestion.suggested.value)
        if idx >= 0:
            self.op_combo.setCurrentIndex(idx)

    def accept(self) -> None:
        if self.hours_spin.value() * 60 + self.mins_spin.value() <= 0:
            QMessageBox.warning(self, "MiLo", _tr("Podaj czas > 0."))
            return
        super().accept()

    def result_stay(self, driver_id: int) -> MiloStay:
        qday = self.day_edit.date()
        op = coerce_op_type(self.op_combo.currentData())
        note = self._stay.note if self._stay else ""
        orig = coerce_op_type(self._orig_op) if self._orig_op is not None else None
        if self._stay is not None and orig is not None and op != orig:
            note = mark_note_manual(note)
        return MiloStay(
            id=self._stay.id if self._stay else None,
            driver_id=driver_id,
            day=date(qday.year(), qday.month(), qday.day()),
            country_code=str(self.country_combo.currentData()),
            minutes=self.hours_spin.value() * 60 + self.mins_spin.value(),
            op_type=op,
            note=note,
        )


def pick_op_type(parent, *, title: str = _tr("Ustaw typ operacji")) -> OpType | None:
    """Prosty wybór typu — None = anulowano."""
    items = list(OP_LABELS_PL.values())
    label, ok = QInputDialog.getItem(parent, title, _tr("Typ:"), items, 0, False)
    if not ok:
        return None
    for op, pl in OP_LABELS_PL.items():
        if pl == label:
            return op
    return None


class MiloSuggestionsDialog(QDialog):
    """Podgląd diff sugestii — aplikacja tylko po OK."""

    def __init__(
        self,
        parent=None,
        *,
        suggestions: list[MiloOpSuggestion],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Sugestie typów MiLo"))
        self.resize(720, 420)
        self._suggestions = suggestions

        hint = QLabel(
            _tr("Heurystyka na podstawie sekwencji krajów. "
            "Nic nie zmienia się, dopóki nie klikniesz Zastosuj. "
            "Sugestia nie zapisuje się sama.")
        )
        hint.setWordWrap(True)

        self.glossary_label = _glossary_label()

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [_tr("Dzień"), _tr("Kraj"), _tr("Teraz"), _tr("Sugestia"), _tr("Powód")]
        )
        self.table.setRowCount(len(suggestions))
        for row, s in enumerate(suggestions):
            self.table.setItem(row, 0, QTableWidgetItem(s.day.strftime("%d.%m.%Y")))
            self.table.setItem(row, 1, QTableWidgetItem(s.country_code))
            self.table.setItem(
                row, 2, QTableWidgetItem(OP_LABELS_PL.get(s.current, s.current.value))
            )
            self.table.setItem(
                row,
                3,
                QTableWidgetItem(OP_LABELS_PL.get(s.suggested, s.suggested.value)),
            )
            self.table.setItem(row, 4, QTableWidgetItem(s.reason))
        self.table.resizeColumnsToContents()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok:
            ok.setText(_tr("Zastosuj sugestie"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.glossary_label)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

    def suggestions(self) -> list[MiloOpSuggestion]:
        return list(self._suggestions)


class MiloSettingsDialog(QDialog):
    """Kursy FX + edycja stawek krajowych."""

    def __init__(self, parent=None, *, store: EwidentorStore) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Ustawienia MiLo — kursy i stawki"))
        self.resize(720, 520)
        self.store = store

        tabs = QTabWidget()
        tabs.addTab(self._fx_tab(), _tr("Kursy walut (→ PLN)"))
        tabs.addTab(self._rates_tab(), _tr("Stawki krajowe"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    def _fx_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self._fx_spins: dict[str, QDoubleSpinBox] = {}
        fx = self.store.get_fx_pln()
        for cur in sorted(c for c in fx if c != "PLN"):
            spin = QDoubleSpinBox()
            spin.setDecimals(4)
            spin.setRange(0.0001, 1000)
            spin.setValue(float(fx[cur]))
            spin.setSuffix(f" PLN / 1 {cur}")
            self._fx_spins[cur] = spin
            form.addRow(cur, spin)
        save = QPushButton(_tr("Zapisz kursy"))
        save.clicked.connect(self._save_fx)
        form.addRow(save)
        self.avg_wage_spin = QDoubleSpinBox()
        self.avg_wage_spin.setDecimals(2)
        self.avg_wage_spin.setRange(0, 100_000)
        self.avg_wage_spin.setSuffix(_tr(" zł"))
        self.avg_wage_spin.setValue(self.store.get_avg_wage_pln())
        form.addRow(_tr("Przeciętne wynagr. (kontekst ZUS)"), self.avg_wage_spin)
        save_avg = QPushButton(_tr("Zapisz przeciętne wynagrodzenie"))
        save_avg.clicked.connect(self._save_avg_wage)
        form.addRow(save_avg)
        form.addRow(
            QLabel(
                _tr("Nadpisanie EUR→PLN na konkretny miesiąc ustawiasz "
                "w zakładce MiLo (pole „Kurs EUR miesiąca”).")
            )
        )
        return w

    def _rates_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.rates_table = QTableWidget(0, 6)
        self.rates_table.setHorizontalHeaderLabels(
            [_tr("Kod"), _tr("Kraj"), _tr("Waluta"), "Stawka/h", _tr("Włączony"), _tr("Uwagi")]
        )
        layout.addWidget(self.rates_table)
        save = QPushButton(_tr("Zapisz stawki z tabeli"))
        save.clicked.connect(self._save_rates)
        layout.addWidget(save)
        self._reload_rates()
        return w

    def _reload_rates(self) -> None:
        rates = self.store.list_country_rates()
        self.rates_table.setRowCount(len(rates))
        for row, r in enumerate(rates):
            self.rates_table.setItem(row, 0, QTableWidgetItem(r.country_code))
            self.rates_table.setItem(row, 1, QTableWidgetItem(r.name_pl))
            self.rates_table.setItem(row, 2, QTableWidgetItem(r.currency))
            self.rates_table.setItem(row, 3, QTableWidgetItem(f"{r.hourly_rate:g}"))
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(
                Qt.CheckState.Checked if r.enabled else Qt.CheckState.Unchecked
            )
            self.rates_table.setItem(row, 4, chk)
            self.rates_table.setItem(row, 5, QTableWidgetItem(r.note))
            self.rates_table.item(row, 0).setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            )
        self.rates_table.resizeColumnsToContents()

    def _save_fx(self) -> None:
        for cur, spin in self._fx_spins.items():
            self.store.set_fx_pln(cur, spin.value())
        QMessageBox.information(self, _tr("Kursy"), _tr("Zapisano kursy walut."))

    def _save_avg_wage(self) -> None:
        self.store.set_avg_wage_pln(self.avg_wage_spin.value())
        QMessageBox.information(self, "ZUS", _tr("Zapisano przeciętne wynagrodzenie."))

    def _save_rates(self) -> None:
        for row in range(self.rates_table.rowCount()):
            code = self.rates_table.item(row, 0).text().strip().upper()
            name = self.rates_table.item(row, 1).text().strip()
            currency = self.rates_table.item(row, 2).text().strip().upper()
            try:
                hourly = float(
                    self.rates_table.item(row, 3).text().strip().replace(",", ".")
                )
            except ValueError:
                QMessageBox.warning(self, _tr("Stawki"), f"{_tr('Błędna stawka w wierszu ')}{row + 1}.")
                return
            enabled = (
                self.rates_table.item(row, 4).checkState() == Qt.CheckState.Checked
            )
            note = self.rates_table.item(row, 5).text().strip()
            self.store.upsert_country_rate(
                CountryRate(
                    country_code=code,
                    name_pl=name,
                    currency=currency,
                    hourly_rate=hourly,
                    enabled=enabled,
                    note=note,
                    source="manual",
                )
            )
        QMessageBox.information(self, _tr("Stawki"), _tr("Zapisano stawki krajowe."))
        self._reload_rates()
