from __future__ import annotations

from spedi.i18n import tr as _tr



from datetime import time

from pathlib import Path



from PySide6.QtCore import QTime

from PySide6.QtWidgets import (

    QCheckBox,

    QComboBox,

    QDialog,

    QDialogButtonBox,

    QDoubleSpinBox,

    QFormLayout,

    QHBoxLayout,

    QLabel,

    QLineEdit,

    QMessageBox,

    QPushButton,

    QTimeEdit,

    QVBoxLayout,

)



from ewidentor.core.brudnopis_import import list_brudnopis_profiles

from ewidentor.core.milo_rates import (

    AT_SENIORITY_LABELS,

    AtSeniority,

    coerce_at_seniority,

)

from ewidentor.core.models import (
    DUTY_MODE_LABELS_PL,
    DUTY_MODE_HEURISTIC,
    DUTY_MODE_RAW,
    DUTY_MODE_SPAN,
    Driver,
    PaySettings,
    normalize_duty_mode,
)



NIGHT_PRESETS: tuple[tuple[str, time, time], ...] = (

    ("00:00–04:00", time(0, 0), time(4, 0)),

    ("22:00–06:00", time(22, 0), time(6, 0)),

    ("21:00–07:00", time(21, 0), time(7, 0)),

)





def _parse_money(text: str) -> float | None:

    """Parsuj kwotę z pola tekstowego (PL/EN: przecinek lub kropka)."""

    raw = (text or "").strip().lower()

    for junk in (_tr("zł/h"), "zl/h", _tr("zł"), "zl", " "):

        raw = raw.replace(junk, "")

    raw = raw.replace("\u00a0", "").replace(",", ".")

    if not raw:

        return 0.0

    try:

        return float(raw)

    except ValueError:

        return None





class DriverDialog(QDialog):

    def __init__(

        self,

        parent=None,

        *,

        driver: Driver | None = None,

        brudnopis_db: Path | None = None,

    ) -> None:

        super().__init__(parent)

        self.setWindowTitle(_tr("Profil kierowcy"))

        self.resize(560, 620)

        self._driver = driver



        self.name_edit = QLineEdit(driver.full_name if driver else "")

        self.card_edit = QLineEdit((driver.card_number or "") if driver else "")

        self.employer_edit = QLineEdit(driver.employer_name if driver else "")

        self.system_edit = QLineEdit(driver.work_system if driver else _tr("równoważny"))

        self.fraction_spin = QDoubleSpinBox()

        self.fraction_spin.setRange(0.1, 1.0)

        self.fraction_spin.setSingleStep(0.1)

        self.fraction_spin.setValue(driver.employment_fraction if driver else 1.0)



        self.profile_combo = QComboBox()

        self.profile_combo.addItem(_tr("(brak powiązania)"), None)

        if brudnopis_db and brudnopis_db.is_file():

            for p in list_brudnopis_profiles(brudnopis_db):

                label = f"{p.driver_name or p.profile_key} [{p.profile_key}]"

                self.profile_combo.addItem(label, p.profile_key)

        if driver and driver.brudnopis_profile_key:

            idx = self.profile_combo.findData(driver.brudnopis_profile_key)

            if idx < 0:

                self.profile_combo.addItem(

                    driver.brudnopis_profile_key, driver.brudnopis_profile_key

                )

                idx = self.profile_combo.findData(driver.brudnopis_profile_key)

            self.profile_combo.setCurrentIndex(max(0, idx))



        pay = driver.pay if driver else PaySettings()

        # QLineEdit zamiast spinboxa — spinbox często „gubi” wpisaną kwotę przy Zapisz.

        self.salary_edit = QLineEdit(f"{pay.monthly_salary:.2f}")

        self.salary_edit.setPlaceholderText("np. 4806")

        self.hourly_edit = QLineEdit(

            "" if pay.hourly_rate is None else f"{pay.hourly_rate:.2f}"

        )

        self.hourly_edit.setPlaceholderText("puste = auto z pensji/normy")



        self.duty_spin = QDoubleSpinBox()

        self.duty_spin.setRange(0, 200)

        self.duty_spin.setSuffix(" %")

        self.duty_spin.setValue(pay.duty_percent)



        self.night_pct_spin = QDoubleSpinBox()

        self.night_pct_spin.setRange(0, 200)

        self.night_pct_spin.setSuffix(" %")

        self.night_pct_spin.setValue(pay.night_percent)



        self.night_start = QTimeEdit()

        self.night_start.setDisplayFormat("HH:mm")

        self.night_start.setTime(QTime(pay.night_start.hour, pay.night_start.minute))

        self.night_end = QTimeEdit()

        self.night_end.setDisplayFormat("HH:mm")

        self.night_end.setTime(QTime(pay.night_end.hour, pay.night_end.minute))



        self.ot50_spin = QDoubleSpinBox()

        self.ot50_spin.setRange(0, 200)

        self.ot50_spin.setSuffix(" %")

        self.ot50_spin.setValue(pay.overtime_50_percent)



        self.ot100_spin = QDoubleSpinBox()

        self.ot100_spin.setRange(0, 200)

        self.ot100_spin.setSuffix(" %")

        self.ot100_spin.setValue(pay.overtime_100_percent)



        self.night_on_avail = QCheckBox(_tr("Licz noc także na dyspozycji / dyżurze"))

        self.night_on_avail.setChecked(bool(pay.night_on_availability))



        self.duty_mode_combo = QComboBox()

        for key, label in DUTY_MODE_LABELS_PL.items():

            self.duty_mode_combo.addItem(label, key)

        mode = normalize_duty_mode(pay.duty_mode)

        idx = self.duty_mode_combo.findData(mode)

        if idx >= 0:

            self.duty_mode_combo.setCurrentIndex(idx)



        self.at_band_combo = QComboBox()

        for band, label in AT_SENIORITY_LABELS.items():

            self.at_band_combo.addItem(label, band)

        if driver:

            idx = self.at_band_combo.findData(driver.at_seniority)

            if idx >= 0:

                self.at_band_combo.setCurrentIndex(idx)



        form = QFormLayout()

        form.addRow(_tr("Imię i nazwisko"), self.name_edit)

        form.addRow(_tr("Nr karty"), self.card_edit)

        form.addRow(_tr("Profil Brudnopis"), self.profile_combo)

        form.addRow(_tr("Pracodawca"), self.employer_edit)

        form.addRow(_tr("System pracy"), self.system_edit)

        form.addRow(_tr("Etat (ułamek)"), self.fraction_spin)

        form.addRow(_tr("Pensja miesięczna (zł)"), self.salary_edit)

        form.addRow(_tr("Stawka godzinowa (zł/h)"), self.hourly_edit)

        form.addRow(_tr("% za dyżur"), self.duty_spin)

        form.addRow("% dodatek nocny", self.night_pct_spin)

        night_row = QHBoxLayout()

        night_row.addWidget(self.night_start)

        night_row.addWidget(self.night_end)

        form.addRow(_tr("Okno nocne (od–do)"), night_row)

        preset_row = QHBoxLayout()

        for label, ns, ne in NIGHT_PRESETS:

            btn = QPushButton(label)

            btn.clicked.connect(

                lambda _=False, a=ns, b=ne: self._apply_night_preset(a, b)

            )

            preset_row.addWidget(btn)

        form.addRow(_tr("Presety nocne"), preset_row)

        form.addRow("", self.night_on_avail)

        form.addRow("% nadgodziny 50", self.ot50_spin)

        form.addRow("% nadgodziny 100", self.ot100_spin)

        form.addRow(_tr("Tryb dyżuru"), self.duty_mode_combo)

        hint = QLabel(

            _tr("Surowy tacho: dyspozycja → dyżur 50%. "

            "Heurystyka: krótka dyspozycja w dniu z jazdą/pracą → praca; "

            "≥9 h dyspozycji → odpoczynek; szum ≤15 min pomijany.")

        )

        hint.setWordWrap(True)

        hint.setObjectName("sectionHint")

        form.addRow(hint)

        form.addRow(_tr("AT staż (MiLo)"), self.at_band_combo)



        buttons = QDialogButtonBox(

            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel

        )

        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)

        if ok_btn is not None:

            ok_btn.setText(_tr("Zapisz"))

        buttons.accepted.connect(self.accept)

        buttons.rejected.connect(self.reject)



        layout = QVBoxLayout(self)

        layout.addLayout(form)

        layout.addWidget(buttons)



    def _apply_night_preset(self, start: time, end: time) -> None:

        self.night_start.setTime(QTime(start.hour, start.minute))

        self.night_end.setTime(QTime(end.hour, end.minute))



    def accept(self) -> None:

        if not self.name_edit.text().strip():

            QMessageBox.warning(self, _tr("Profil"), _tr("Podaj imię i nazwisko."))

            return

        if _parse_money(self.salary_edit.text()) is None:

            QMessageBox.warning(self, _tr("Profil"), _tr("Niepoprawna pensja miesięczna."))

            return

        hourly_txt = self.hourly_edit.text().strip()

        if hourly_txt and _parse_money(hourly_txt) is None:

            QMessageBox.warning(self, _tr("Profil"), _tr("Niepoprawna stawka godzinowa."))

            return

        self.fraction_spin.interpretText()

        self.duty_spin.interpretText()

        self.night_pct_spin.interpretText()

        self.ot50_spin.interpretText()

        self.ot100_spin.interpretText()

        super().accept()



    def result_driver(self) -> Driver:

        self.fraction_spin.interpretText()

        self.duty_spin.interpretText()

        self.night_pct_spin.interpretText()

        self.ot50_spin.interpretText()

        self.ot100_spin.interpretText()

        ns = self.night_start.time()

        ne = self.night_end.time()

        salary = _parse_money(self.salary_edit.text()) or 0.0

        hourly_txt = self.hourly_edit.text().strip()

        hourly = _parse_money(hourly_txt) if hourly_txt else None

        if hourly is not None and hourly <= 0:

            hourly = None

        duty_mode = normalize_duty_mode(self.duty_mode_combo.currentData() or DUTY_MODE_RAW)

        if duty_mode not in (DUTY_MODE_RAW, DUTY_MODE_HEURISTIC, DUTY_MODE_SPAN):

            duty_mode = DUTY_MODE_RAW

        return Driver(

            id=self._driver.id if self._driver else None,

            full_name=self.name_edit.text().strip(),

            card_number=self.card_edit.text().strip() or None,

            brudnopis_profile_key=self.profile_combo.currentData(),

            work_system=self.system_edit.text().strip() or _tr("równoważny"),

            employment_fraction=self.fraction_spin.value(),

            employer_name=self.employer_edit.text().strip(),

            notes=self._driver.notes if self._driver else "",

            at_seniority=coerce_at_seniority(self.at_band_combo.currentData()),

            pay=PaySettings(

                monthly_salary=salary,

                hourly_rate=hourly,

                duty_percent=self.duty_spin.value(),

                night_percent=self.night_pct_spin.value(),

                night_start=time(ns.hour(), ns.minute()),

                night_end=time(ne.hour(), ne.minute()),

                overtime_50_percent=self.ot50_spin.value(),

                overtime_100_percent=self.ot100_spin.value(),

                night_on_availability=self.night_on_avail.isChecked(),

                duty_mode=str(duty_mode),

            ),

        )


