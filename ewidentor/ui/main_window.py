from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ewidentor.core.brudnopis_import import (
    RawSegment,
    load_month_border_crossings,
    load_month_places,
    load_month_segments,
    sync_drivers_from_brudnopis,
)
from ewidentor.core.comp_ledger import (
    COMP_LABELS_PL,
    CompEntry,
    CompKind,
    detect_holiday_wn_debts,
    ensure_open_wn_entries,
    entry_status_pl,
    unreturned_sunday_holiday_minutes,
)
from ewidentor.core.country_minutes import (
    country_labels_from_places,
    derive_country_minutes,
    presence_first_seen_map,
)
from ewidentor.core.day_overrides import apply_day_overrides
from ewidentor.core.ddd_identity import (
    DddIdentityError,
    ddd_analyzer_available,
    read_ddd_identity,
)
from ewidentor.core.dispatcher_milo import (
    DispatcherLeg,
    classify_stay_from_dispatcher,
    load_dispatcher_legs_for_driver,
)
from ewidentor.core.display_tz import (
    DEFAULT_ZONE,
    SETTINGS_APP,
    SETTINGS_KEY,
    SETTINGS_ORG,
    normalize_zone,
    zone_label,
)
from ewidentor.core.milo import MiloStay, compute_milo_topup, sorted_milo_stays
from ewidentor.core.milo_rates import OP_LABELS_PL, AtSeniority, OpType
from ewidentor.core.milo_suggest import (
    dispatcher_hint_for_stay,
    format_suggestion_hint,
    mark_note_manual,
    suggestion_for_stay,
    suggest_milo_op_types,
)
from ewidentor.core.models import (
    ABSENCE_LABELS_PL,
    Absence,
    AbsenceKind,
    Driver,
    MonthCard,
    PaySettings,
)
from ewidentor.core.month_card import build_month_card, format_clock_local, format_minutes
from ewidentor.core.paths import default_brudnopis_db, driver_reports_dir, reports_dir
from ewidentor.core.payroll import compute_payroll
from ewidentor.core.report_pdf import default_report_path, export_month_pdf
from ewidentor.core.store import EwidentorStore
from ewidentor.core.virtual_diet import compute_virtual_diet
from ewidentor.ui.day_editor_dialog import DayEditorDialog
from ewidentor.ui.driver_dialog import DriverDialog
from ewidentor.ui.milo_dialogs import (
    MiloSettingsDialog,
    MiloStayDialog,
    MiloSuggestionsDialog,
    pick_op_type,
)
from ewidentor.ui import theme as T
from ewidentor.ui.widgets import (
    ChecklistState,
    DayInspector,
    KpiStrip,
    MonthCalendar,
    MonthChecklist,
)

WEEKDAY_PL = (
    _tr("poniedziałek"),
    _tr("wtorek"),
    _tr("środa"),
    _tr("czwartek"),
    _tr("piątek"),
    _tr("sobota"),
    _tr("niedziela"),
)

_COLOR_ABSENCE = QColor(T.ABSENCE_BG)
_COLOR_MANUAL = QColor(T.MANUAL_BG)
_COLOR_WN = QColor(T.WN_BG)
_COLOR_NO_COUNTRY = QColor(T.NO_COUNTRY_BG)


class AbsenceDialog(QDialog):
    def __init__(self, parent=None, *, default_day: date | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Nieobecność"))
        self.kind_combo = QComboBox()
        for kind, label in ABSENCE_LABELS_PL.items():
            self.kind_combo.addItem(label, kind)
        self.day_edit = QDateEdit()
        self.day_edit.setCalendarPopup(True)
        self.day_edit.setDisplayFormat("yyyy-MM-dd")
        if default_day:
            self.day_edit.setDate(default_day)
        self.title_line = QLineEdit()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow(_tr("Rodzaj"), self.kind_combo)
        form.addRow(_tr("Dzień"), self.day_edit)
        form.addRow(_tr("Tytuł"), self.title_line)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def result_absence(self, driver_id: int) -> Absence:
        qday = self.day_edit.date()
        day = date(qday.year(), qday.month(), qday.day())
        kind = self.kind_combo.currentData()
        return Absence(
            id=None,
            driver_id=driver_id,
            day=day,
            kind=kind,
            title=self.title_line.text().strip(),
        )


class SettleWnDialog(QDialog):
    def __init__(self, parent=None, *, default_day: date | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Oddaj dzień Wn"))
        self.day_edit = QDateEdit()
        self.day_edit.setCalendarPopup(True)
        self.day_edit.setDisplayFormat("yyyy-MM-dd")
        self.day_edit.setDate(default_day or date.today())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow(_tr("Dzień oddania (Wn)"), self.day_edit)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def settle_day(self) -> date:
        qday = self.day_edit.date()
        return date(qday.year(), qday.month(), qday.day())


class CloseMonthDialog(QDialog):
    def __init__(self, parent, state: ChecklistState) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Zamknij miesiąc"))
        self.resize(420, 280)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_tr("Status przed generowaniem PDF:")))
        checks = [
            (_tr("Sync / Brudnopis"), state.sync_ok),
            (_tr("Dni z aktywnością"), state.days_ok),
            (_tr("Wn rozliczone"), state.wn_ok),
            (_tr("MiLo typy OK"), state.milo_ok),
            (_tr("Załączniki"), state.attachments_ok),
            (_tr("PDF wcześniej"), state.pdf_done),
        ]
        for label, ok in checks:
            mark = "OK" if ok else "do sprawdzenia"
            lay.addWidget(QLabel(f"• {label}: {mark}"))
        lay.addWidget(
            QLabel(
                _tr("Możesz wygenerować PDF mimo braków — checklista jest pomocnicza.")
            )
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_tr("Generuj PDF…"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self, parent=None, *, brudnopis_db: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Ewidentor — ewidencja czasu pracy"))
        self.resize(1440, 900)
        if parent is not None:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.store = EwidentorStore()
        self._card: MonthCard | None = None
        self._countries_by_day: dict[date, str] = {}
        self._segments: list[RawSegment] = []
        self._manual_days: set[date] = set()
        self._selected_day: date | None = None
        self._pdf_done_for: tuple[int, int, int] | None = None
        self._loading = False
        if brudnopis_db is not None:
            self._brudnopis_db = Path(brudnopis_db)
            self.store.set_setting("brudnopis_db", str(self._brudnopis_db))
        else:
            self._brudnopis_db = Path(
                self.store.get_setting("brudnopis_db", str(default_brudnopis_db()))
            )
        self._zone = normalize_zone(
            QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_KEY, DEFAULT_ZONE)
        )
        self._comp_entries: list[CompEntry] = []
        self._milo_stays: list[MiloStay] = []
        self._dispatcher_legs: list[DispatcherLeg] = []
        self._last_checklist = ChecklistState()

        self._autoload_timer = QTimer(self)
        self._autoload_timer.setSingleShot(True)
        self._autoload_timer.setInterval(200)
        self._autoload_timer.timeout.connect(self._auto_load_month)

        self._build_widgets()
        self._build_layout()
        self._build_toolbar()
        self.reload_drivers()
        if self.driver_combo.count() > 0:
            self._schedule_autoload()

    def _build_widgets(self) -> None:
        self.driver_combo = QComboBox()
        self.year_spin = QSpinBox()
        self.year_spin.setRange(2020, 2040)
        self.year_spin.setValue(date.today().year)
        self.month_spin = QSpinBox()
        self.month_spin.setRange(1, 12)
        self.month_spin.setValue(date.today().month)
        self.driver_combo.currentIndexChanged.connect(self._schedule_autoload)
        self.year_spin.valueChanged.connect(self._schedule_autoload)
        self.month_spin.valueChanged.connect(self._schedule_autoload)

        self.kpi_strip = KpiStrip()
        self.checklist = MonthChecklist()
        self.checklist.jump_requested.connect(self._on_checklist_jump)
        self.checklist.close_month_requested.connect(self.close_month)

        self.view_list_btn = QPushButton(_tr("Lista"))
        self.view_cal_btn = QPushButton(_tr("Kalendarz"))
        self.view_list_btn.setObjectName("viewToggle")
        self.view_cal_btn.setObjectName("viewToggle")
        self.view_list_btn.setCheckable(True)
        self.view_cal_btn.setCheckable(True)
        self.view_list_btn.setChecked(True)
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_group.addButton(self.view_list_btn)
        self._view_group.addButton(self.view_cal_btn)
        self.view_list_btn.clicked.connect(lambda: self._set_view(0))
        self.view_cal_btn.clicked.connect(lambda: self._set_view(1))

        self.days_table = QTableWidget(0, 12)
        self.days_table.setHorizontalHeaderLabels(
            [
                _tr("Dzień"),
                _tr("Znaczniki"),
                _tr("Kraje (DDD)"),
                "Start",
                _tr("Koniec"),
                _tr("Czas pracy"),
                _tr("Jazda"),
                _tr("Inna praca"),
                _tr("Dyżur 50%"),
                _tr("Noc"),
                _tr("Nadg."),
                _tr("Nieobecność"),
            ]
        )
        self.days_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.days_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.days_table.itemSelectionChanged.connect(self._on_table_selection)
        self.days_table.doubleClicked.connect(self.edit_selected_day)

        self.month_calendar = MonthCalendar()
        self.month_calendar.day_selected.connect(self._select_day)

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.days_table)
        self.view_stack.addWidget(self.month_calendar)

        self.day_inspector = DayInspector()
        self.day_inspector.edit_requested.connect(self.edit_selected_day)

        self.abs_table = QTableWidget(0, 3)
        self.abs_table.setHorizontalHeaderLabels([_tr("Dzień"), _tr("Rodzaj"), _tr("Tytuł")])
        self.abs_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        self.comp_table = QTableWidget(0, 4)
        self.comp_table.setHorizontalHeaderLabels(
            [_tr("Dzień długu"), _tr("Rodzaj"), _tr("Opis"), "Status"]
        )
        self.comp_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.comp_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.milo_table = QTableWidget(0, 5)
        self.milo_table.setHorizontalHeaderLabels(
            [_tr("Dzień"), _tr("Kraj"), _tr("Typ"), _tr("Czas MiLo"), _tr("Sugestia")]
        )
        self.milo_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.milo_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.milo_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.milo_table.doubleClicked.connect(lambda *_: self.edit_milo_stay())

        self.att_table = QTableWidget(0, 2)
        self.att_table.setHorizontalHeaderLabels([_tr("Plik"), _tr("Uwagi")])

        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self._wrap_tab_absences(), _tr("Nieobecności"))
        self.right_tabs.addTab(self._wrap_tab_wn(), "Wn / Xb")
        self.right_tabs.addTab(self._wrap_tab_milo(), "MiLo")
        self.right_tabs.addTab(self._wrap_tab_attachments(), _tr("Załączniki"))

    def _wrap_tab_absences(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        btns = QHBoxLayout()
        b_add = QPushButton(_tr("Dodaj"))
        b_add.clicked.connect(self.add_absence)
        b_del = QPushButton(_tr("Usuń"))
        b_del.clicked.connect(self.delete_absence)
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addWidget(self.abs_table, 1)
        return w

    def _wrap_tab_wn(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        btns = QHBoxLayout()
        b_settle = QPushButton(_tr("Oddaj dzień"))
        b_settle.clicked.connect(self.settle_wn)
        b_pay = QPushButton(_tr("Wypłać 100%"))
        b_pay.clicked.connect(self.pay_wn_100)
        b_del = QPushButton(_tr("Usuń wpis"))
        b_del.clicked.connect(self.delete_comp_entry)
        btns.addWidget(b_settle)
        btns.addWidget(b_pay)
        btns.addWidget(b_del)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addWidget(self.comp_table, 1)
        return w

    def _wrap_tab_milo(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(
            _tr("Top-up: kabotaż/cross-trade albo cały czas pracy w kraju (jak wyrównanie na karcie). "
            "Auto = bilateralny. Checklist „MiLo typy OK” = brak wpisów auto-bilateralnych. "
            "Kolumna sugestii (zielona) nic nie zapisuje — typ zmieniasz sam albo „Ustaw jak sugestia”.")
        )
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        fx_row = QHBoxLayout()
        fx_row.addWidget(QLabel(_tr("Kurs EUR miesiąca:")))
        self.milo_fx_spin = QDoubleSpinBox()
        self.milo_fx_spin.setDecimals(4)
        self.milo_fx_spin.setRange(0.0001, 1000)
        self.milo_fx_spin.setSuffix(" PLN")
        self.milo_fx_spin.setValue(4.30)
        fx_row.addWidget(self.milo_fx_spin)
        b_fx = QPushButton(_tr("Zapisz kurs miesiąca"))
        b_fx.clicked.connect(self.save_month_fx_eur)
        fx_row.addWidget(b_fx)
        b_fx_clear = QPushButton(_tr("Wyczyść nadpisanie"))
        b_fx_clear.clicked.connect(self.clear_month_fx_eur)
        fx_row.addWidget(b_fx_clear)
        fx_row.addStretch(1)
        lay.addLayout(fx_row)

        self.milo_country_hours = QCheckBox(
            _tr("Wyrównanie od całego czasu pracy w kraju (nie tylko kabotaż/cross-trade)")
        )
        self.milo_country_hours.setChecked(
            self.store.get_setting("milo_topup_mode", "posting") == "country_hours"
        )
        self.milo_country_hours.stateChanged.connect(self._on_milo_mode_changed)
        lay.addWidget(self.milo_country_hours)

        self.milo_preview_label = QLabel("")
        self.milo_preview_label.setObjectName("sectionHint")
        self.milo_preview_label.setWordWrap(True)
        lay.addWidget(self.milo_preview_label)

        btns = QHBoxLayout()
        b_add = QPushButton(_tr("Dodaj"))
        b_add.clicked.connect(self.add_milo_stay)
        b_edit = QPushButton(_tr("Edytuj"))
        b_edit.clicked.connect(self.edit_milo_stay)
        b_del = QPushButton(_tr("Usuń"))
        b_del.clicked.connect(self.delete_milo_stay)
        b_type = QPushButton(_tr("Ustaw typ…"))
        b_type.clicked.connect(self.bulk_set_milo_type)
        b_auto = QPushButton(_tr("Oznacz wszystkie auto jako…"))
        b_auto.clicked.connect(self.bulk_set_auto_milo_type)
        b_sug = QPushButton(_tr("Zastosuj sugestie…"))
        b_sug.clicked.connect(self.apply_milo_suggestions)
        btns.addWidget(b_add)
        btns.addWidget(b_edit)
        btns.addWidget(b_del)
        btns.addWidget(b_type)
        btns.addWidget(b_auto)
        btns.addWidget(b_sug)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addWidget(self.milo_table, 1)
        return w

    def _wrap_tab_attachments(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        hint = QLabel(_tr("JPG/PNG = 1 strona w PDF; wielostronicowy PDF = wszystkie strony."))
        hint.setObjectName("sectionHint")
        lay.addWidget(hint)
        btns = QHBoxLayout()
        b_add = QPushButton(_tr("Dodaj plik"))
        b_add.clicked.connect(self.add_attachment)
        b_del = QPushButton(_tr("Usuń"))
        b_del.clicked.connect(self.delete_attachment)
        btns.addWidget(b_add)
        btns.addWidget(b_del)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addWidget(self.att_table, 1)
        return w

    def _build_layout(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        context = QHBoxLayout()
        context.setSpacing(8)
        context.addWidget(QLabel(_tr("Kierowca:")))
        context.addWidget(self.driver_combo, 1)
        context.addWidget(QLabel(_tr("Rok:")))
        context.addWidget(self.year_spin)
        context.addWidget(QLabel(_tr("Miesiąc:")))
        context.addWidget(self.month_spin)
        context.addWidget(self.view_list_btn)
        context.addWidget(self.view_cal_btn)
        root.addLayout(context)

        root.addWidget(self.kpi_strip)
        root.addWidget(self.checklist)

        split = QSplitter()
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(0)
        left_l.addWidget(self.view_stack, 1)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(4)
        right_l.addWidget(self.day_inspector)
        right_l.addWidget(self.right_tabs, 1)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)
        self.setCentralWidget(central)

    def _build_toolbar(self) -> None:
        tb = QToolBar(_tr("Główne"))
        tb.setMovable(False)
        self.addToolBar(tb)

        brand = QLabel(_tr("Ewidentor"))
        brand.setObjectName("brandWordmark")
        tb.addWidget(brand)
        tb.addSeparator()

        def add_group(title: str, actions: list[QAction]) -> None:
            tb.addWidget(QLabel(f"  {title}: "))
            for a in actions:
                tb.addAction(a)
            tb.addSeparator()

        act_new = QAction(_tr("Nowy"), self)
        act_new.triggered.connect(self.new_driver)
        act_edit = QAction(_tr("Edytuj"), self)
        act_edit.triggered.connect(self.edit_driver)
        act_del = QAction(_tr("Usuń"), self)
        act_del.triggered.connect(self.delete_driver)
        add_group(_tr("Profil"), [act_new, act_edit, act_del])

        act_sync = QAction(_tr("Zaciągnij z Brudnopisu"), self)
        act_sync.triggered.connect(self.sync_from_brudnopis)
        act_ddd = QAction("DDD…", self)
        act_ddd.triggered.connect(self.import_ddd_identity)
        act_db = QAction(_tr("Ścieżka DB…"), self)
        act_db.triggered.connect(self.pick_brudnopis_db)
        add_group(_tr("Dane"), [act_sync, act_ddd, act_db])

        act_milo = QAction(_tr("MiLo: kursy…"), self)
        act_milo.triggered.connect(self.open_milo_settings)
        add_group(_tr("Rozliczenie"), [act_milo])

        act_pdf = QAction(_tr("PDF ewidencji"), self)
        act_pdf.triggered.connect(self.export_pdf)
        act_close = QAction(_tr("Zamknij miesiąc…"), self)
        act_close.triggered.connect(self.close_month)
        add_group(_tr("PDF / Zamknij"), [act_pdf, act_close])

        self._zone_combo = QComboBox()
        self._zone_combo.addItem(zone_label("Europe/Warsaw"), "Europe/Warsaw")
        self._zone_combo.addItem(zone_label("UTC"), "UTC")
        zidx = self._zone_combo.findData(self._zone)
        if zidx >= 0:
            self._zone_combo.setCurrentIndex(zidx)
        self._zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        tb.addWidget(QLabel(_tr(" Strefa: ")))
        tb.addWidget(self._zone_combo)
        for btn in tb.findChildren(QToolButton):
            btn.setAutoRaise(False)

    def _set_view(self, index: int) -> None:
        self.view_stack.setCurrentIndex(index)
        self.view_list_btn.setChecked(index == 0)
        self.view_cal_btn.setChecked(index == 1)

    def _schedule_autoload(self) -> None:
        if self._loading:
            return
        self._autoload_timer.start()

    def _auto_load_month(self) -> None:
        if self.current_driver_id() is None:
            return
        self.load_month(silent=True)

    def _on_zone_changed(self) -> None:
        self._zone = normalize_zone(self._zone_combo.currentData())
        QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_KEY, self._zone)
        if self._card is not None:
            self._fill_days()
            if self._selected_day:
                self._refresh_inspector(self._selected_day)

    def _on_table_selection(self) -> None:
        row = self.days_table.currentRow()
        if row < 0:
            return
        item = self.days_table.item(row, 0)
        if not item:
            return
        raw = item.data(Qt.ItemDataRole.UserRole)
        if not raw:
            return
        self._select_day(date.fromisoformat(str(raw)), from_table=True)

    def _select_day(self, day: date, *, from_table: bool = False) -> None:
        self._selected_day = day
        self.month_calendar.set_selected_day(day)
        if not from_table and self._card is not None:
            for row in range(self.days_table.rowCount()):
                item = self.days_table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == day.isoformat():
                    self.days_table.blockSignals(True)
                    self.days_table.selectRow(row)
                    self.days_table.blockSignals(False)
                    break
        self._refresh_inspector(day)

    def _day_badges(self, d) -> list[str]:
        badges: list[str] = []
        if d.absence_label:
            badges.append(_tr("nieobecność"))
        if d.is_manual:
            badges.append(_tr("ręczny"))
        if any(
            e.kind == CompKind.SUNDAY_HOLIDAY_WN
            and e.earn_day == d.day
            and not e.paid_out
            and e.settle_day is None
            for e in self._comp_entries
        ):
            badges.append("Wn")
        countries = self._countries_by_day.get(d.day, "")
        if d.work_min > 0 and not countries.strip():
            badges.append("brak kraju")
        return badges

    def _refresh_inspector(self, day: date) -> None:
        if self._card is None:
            self.day_inspector.clear()
            return
        day_card = next((x for x in self._card.days if x.day == day), None)
        if day_card is None:
            self.day_inspector.clear()
            return
        segs = [s for s in self._segments if s.day == day]
        blocks = ", ".join(
            f"{s.activity} {s.start_minute // 60:02d}:{s.start_minute % 60:02d}"
            f"–{s.end_minute // 60:02d}:{s.end_minute % 60:02d}"
            for s in segs[:8]
        )
        if len(segs) > 8:
            blocks += f" (+{len(segs) - 8})"
        self.day_inspector.show_day(
            day,
            weekday_pl=WEEKDAY_PL[day.weekday()],
            countries=self._countries_by_day.get(day, "—"),
            start=format_clock_local(day, day_card.start_min, self._zone),
            end=format_clock_local(day, day_card.end_min, self._zone),
            work=format_minutes(day_card.work_min),
            drive=format_minutes(day_card.drive_min),
            duty=format_minutes(day_card.duty_50_min),
            night=format_minutes(day_card.night_min),
            overtime=format_minutes(day_card.overtime_min),
            absence=day_card.absence_label,
            badges=self._day_badges(day_card),
            blocks_summary=blocks,
        )

    def _on_checklist_jump(self, key: str) -> None:
        if key == "sync":
            self.sync_from_brudnopis()
        elif key == "days":
            self._set_view(0)
        elif key == "wn":
            self.right_tabs.setCurrentIndex(1)
        elif key == "milo":
            self.right_tabs.setCurrentIndex(2)
        elif key == "attachments":
            self.right_tabs.setCurrentIndex(3)
        elif key == "pdf":
            self.export_pdf()

    def _update_checklist(self, *, segments_count: int, pay_work: int) -> None:
        did = self.current_driver_id()
        driver = self.store.get_driver(did) if did is not None else None
        sync_ok = bool(
            driver
            and driver.brudnopis_profile_key
            and self.brudnopis_db().is_file()
            and segments_count > 0
        )
        days_ok = pay_work > 0
        wn_open = sum(
            1
            for e in self._comp_entries
            if e.kind == CompKind.SUNDAY_HOLIDAY_WN
            and not e.paid_out
            and e.settle_day is None
        )
        wn_ok = wn_open == 0
        # MiLo OK when no auto-bilateral stays still waiting for review/type change.
        auto_bilateral = [
            s
            for s in self._milo_stays
            if s.op_type == OpType.BILATERAL and "auto" in (s.note or "").lower()
        ]
        milo_ok = len(auto_bilateral) == 0

        atts = []
        if did is not None and self._card is not None:
            atts = self.store.list_attachments(did, self._card.year, self._card.month)
        attachments_ok = len(atts) > 0
        pdf_done = False
        if did is not None and self._card is not None:
            pdf_done = self._pdf_done_for == (did, self._card.year, self._card.month)

        self._last_checklist = ChecklistState(
            sync_ok=sync_ok,
            days_ok=days_ok,
            wn_ok=wn_ok,
            milo_ok=milo_ok,
            attachments_ok=attachments_ok,
            pdf_done=pdf_done,
        )
        self.checklist.set_state(self._last_checklist)

    def brudnopis_db(self) -> Path:
        return self._brudnopis_db

    def sync_from_brudnopis(self) -> None:
        db = self.brudnopis_db()
        if not db.is_file():
            QMessageBox.warning(
                self,
                _tr("Brudnopis"),
                f"Nie znaleziono bazy:\n{db}{_tr('\n\nUstaw ścieżkę (toolbar).')}",
            )
            return
        result = sync_drivers_from_brudnopis(self.store, db, only_card=True)
        self.reload_drivers()
        details = "\n".join(result.messages[:12])
        if len(result.messages) > 12:
            details += f"\n… (+{len(result.messages) - 12})"
        QMessageBox.information(
            self,
            _tr("Import z Brudnopisu"),
            f"{_tr('Dodano: ')}{result.created}\nZaktualizowano: {result.updated}{_tr('\nBez zmian / pominięte: ')}{result.skipped}\n\n{details}",
        )
        self._schedule_autoload()

    def import_ddd_identity(self) -> None:
        if not ddd_analyzer_available():
            QMessageBox.warning(
                self,
                "DDD",
                _tr("Brak ddd-analyzer.\n"
                "Zainstaluj obok projektu:\n"
                "pip install -e ..\\ddd-analyzer\n\n"
                "Albo najpierw zaimportuj kartę w Brudnopisie OT "
                "i użyj „Zaciągnij z Brudnopisu”."),
            )
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            _tr("Pliki DDD (karta kierowcy)"),
            "",
            "DDD (*.ddd);;All (*.*)",
        )
        if not paths:
            return
        created = updated = failed = 0
        notes: list[str] = []
        for path in paths:
            try:
                ident = read_ddd_identity(path)
            except DddIdentityError as exc:
                failed += 1
                notes.append(f"{Path(path).name}: {exc}")
                continue
            if ident.source_kind != "card" or not ident.card_number:
                failed += 1
                notes.append(f"{Path(path).name}: to nie karta kierowcy / brak nr karty")
                continue
            existing = self.store.find_driver_match(
                profile_key=ident.profile_key,
                card_number=ident.card_number,
            )
            if existing is None:
                self.store.upsert_driver(
                    Driver(
                        id=None,
                        full_name=ident.driver_name,
                        card_number=ident.card_number,
                        brudnopis_profile_key=ident.profile_key,
                        notes=f"{_tr('Tożsamość z DDD: ')}{ident.filename}",
                        pay=PaySettings(),
                    )
                )
                created += 1
                notes.append(f"{_tr('Dodano: ')}{ident.driver_name}")
            else:
                existing.full_name = ident.driver_name or existing.full_name
                existing.card_number = ident.card_number
                existing.brudnopis_profile_key = (
                    existing.brudnopis_profile_key or ident.profile_key
                )
                self.store.upsert_driver(existing)
                updated += 1
                notes.append(f"{_tr('Zaktualizowano: ')}{existing.full_name}")
        self.reload_drivers()
        QMessageBox.information(
            self,
            "Import DDD",
            f"{_tr('Dodano: ')}{created}, zaktualizowano: {updated}{_tr(', błędy: ')}{failed}\n\n"
            + "\n".join(notes[:10]),
        )
        self._schedule_autoload()

    def pick_brudnopis_db(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("Baza Brudnopisu (ddd_profiles.sqlite)"),
            str(self._brudnopis_db.parent if self._brudnopis_db else Path.home()),
            "SQLite (*.sqlite);;All (*.*)",
        )
        if path:
            self._brudnopis_db = Path(path)
            self.store.set_setting("brudnopis_db", path)
            QMessageBox.information(self, _tr("Brudnopis"), f"Ustawiono:\n{path}")
            self._schedule_autoload()

    def reload_drivers(self) -> None:
        current = self.driver_combo.currentData()
        self.driver_combo.blockSignals(True)
        self.driver_combo.clear()
        for d in self.store.list_drivers():
            label = d.full_name
            if d.card_number:
                label = f"{d.full_name} ({d.card_number})"
            self.driver_combo.addItem(label, d.id)
        if current is not None:
            idx = self.driver_combo.findData(current)
            if idx >= 0:
                self.driver_combo.setCurrentIndex(idx)
        self.driver_combo.blockSignals(False)

    def current_driver_id(self) -> int | None:
        return self.driver_combo.currentData()

    def new_driver(self) -> None:
        dlg = DriverDialog(self, brudnopis_db=self.brudnopis_db())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        driver = dlg.result_driver()
        self.store.upsert_driver(driver)
        self.reload_drivers()
        idx = self.driver_combo.findData(driver.id)
        if idx >= 0:
            self.driver_combo.setCurrentIndex(idx)
        self._schedule_autoload()

    def edit_driver(self) -> None:
        did = self.current_driver_id()
        if did is None:
            QMessageBox.information(self, _tr("Profil"), _tr("Wybierz kierowcę."))
            return
        driver = self.store.get_driver(did)
        if not driver:
            return
        dlg = DriverDialog(self, driver=driver, brudnopis_db=self.brudnopis_db())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dlg.result_driver()
        try:
            self.store.upsert_driver(updated)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _tr("Profil"), f"{_tr('Nie udało się zapisać profilu:\n')}{exc}")
            return
        verify = self.store.get_driver(updated.id) if updated.id is not None else None
        self.reload_drivers()
        self.load_month(silent=True)
        if verify is not None:
            hourly = (
                f"{verify.pay.hourly_rate:.2f}{_tr(' zł/h')}"
                if verify.pay.hourly_rate is not None
                else "auto z pensji/normy"
            )
            QMessageBox.information(
                self,
                _tr("Profil zapisany"),
                f"{verify.full_name}\nPensja: {verify.pay.monthly_salary:,.2f}{_tr(' zł\nStawka: ')}{hourly}".replace(",", " "),
            )

    def delete_driver(self) -> None:
        did = self.current_driver_id()
        if did is None:
            return
        if (
            QMessageBox.question(self, _tr("Usuń"), _tr("Usunąć profil kierowcy?"))
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.store.delete_driver(did)
        self._card = None
        self.kpi_strip.clear()
        self.day_inspector.clear()
        self.reload_drivers()
        self.days_table.setRowCount(0)

    def load_month(self, *, silent: bool = False) -> None:
        did = self.current_driver_id()
        if did is None:
            if not silent:
                QMessageBox.information(self, _tr("Miesiąc"), _tr("Dodaj i wybierz kierowcę."))
            return
        driver = self.store.get_driver(did)
        if not driver:
            return
        self._loading = True
        try:
            year, month = self.year_spin.value(), self.month_spin.value()
            segments: list[RawSegment] = []
            crossings = []
            places = []
            if driver.brudnopis_profile_key:
                db = self.brudnopis_db()
                segments = load_month_segments(
                    db, driver.brudnopis_profile_key, year, month
                )
                crossings = load_month_border_crossings(
                    db, driver.brudnopis_profile_key, year, month
                )
                places = load_month_places(
                    db, driver.brudnopis_profile_key, year, month
                )
            overrides = self.store.list_day_overrides(did, year, month)
            self._manual_days = set(overrides.keys())
            segments = apply_day_overrides(segments, overrides)
            self._segments = segments
            absences = self.store.list_absences(did, year, month)
            self._card = build_month_card(
                driver,
                year,
                month,
                segments,
                absences,
                manual_days=self._manual_days,
            )
            self._sync_wn_entries()
            self._dispatcher_legs = load_dispatcher_legs_for_driver(
                driver.full_name, year, month
            )
            self._auto_fill_milo_from_borders(did, segments, crossings, places)
            self._milo_stays = self._ordered_milo_stays(
                self.store.list_milo_stays(did, year, month),
                segments,
                crossings,
                places,
            )
            self._fill_days()
            self._fill_calendar()
            self._fill_absences(absences)
            self._fill_comp()
            self._fill_milo()
            self._fill_attachments()
            sunday_u = unreturned_sunday_holiday_minutes(self._card, self._comp_entries)
            year, month = self._card.year, self._card.month
            fx = self.store.get_fx_pln(year=year, month=month)
            self._sync_milo_fx_spin(fx.get("EUR", 4.30), year, month)
            pay_base = compute_payroll(self._card, sunday_unreturned_min=sunday_u)
            milo = compute_milo_topup(
                self._milo_stays,
                pl_hourly_pln=pay_base.hourly_rate,
                rates=self.store.list_country_rates(),
                fx_pln=fx,
                at_band=driver.at_seniority,
                posting_only=self._milo_posting_only(),
            )
            pay = compute_payroll(
                self._card,
                sunday_unreturned_min=sunday_u,
                milo_topup_pln=milo.topup_total_pln,
            )
            wn_open = sum(
                1
                for e in self._comp_entries
                if e.kind == CompKind.SUNDAY_HOLIDAY_WN
                and not e.paid_out
                and e.settle_day is None
            )
            milo_kpi = f"{pay.milo_topup_pln:,.2f}{_tr(' zł')}".replace(",", " ")
            auto_n = sum(
                1
                for s in self._milo_stays
                if s.op_type == OpType.BILATERAL and "auto" in (s.note or "").lower()
            )
            if auto_n > 0:
                dry = compute_milo_topup(
                    [
                        MiloStay(
                            id=s.id,
                            driver_id=s.driver_id,
                            day=s.day,
                            country_code=s.country_code,
                            minutes=s.minutes,
                            op_type=OpType.CROSS_TRADE
                            if s.country_code.upper() != "PL"
                            else s.op_type,
                            note=s.note,
                        )
                        for s in self._milo_stays
                    ],
                    pl_hourly_pln=pay_base.hourly_rate,
                    rates=self.store.list_country_rates(),
                    fx_pln=fx,
                    at_band=driver.at_seniority,
                    posting_only=self._milo_posting_only(),
                )
                milo_kpi += (
                    f"\n≈CT {dry.topup_total_pln:,.2f}{_tr(' zł')}".replace(",", " ")
                )
                self.milo_preview_label.setText(
                    f"Auto-bilateral: {auto_n}{_tr(' wpisów. Szacunek gdyby wszystkie (≠PL) były cross-trade: ')}{dry.topup_total_pln:,.2f}{_tr(' zł (bez zapisu).')}".replace(",", " ")
                )
            else:
                self.milo_preview_label.setText("")
            work_kpi = (
                f"{format_minutes(pay.work_min)}{_tr(' · dyżur ')}{format_minutes(pay.duty_50_min)}"
            )
            self.kpi_strip.update_values(
                norma=format_minutes(pay.norma_min),
                work=work_kpi,
                wn_open=str(wn_open),
                milo=milo_kpi,
                gross=f"{pay.gross_total:,.2f}{_tr(' zł')}".replace(",", " "),
                net=f"{pay.net_estimate:,.2f}{_tr(' zł')}".replace(",", " "),
            )
            self._update_checklist(segments_count=len(segments), pay_work=pay.work_min)
            if self._selected_day and any(d.day == self._selected_day for d in self._card.days):
                self._refresh_inspector(self._selected_day)
            else:
                self._selected_day = None
                self.day_inspector.clear()
        finally:
            self._loading = False

    def _fill_days(self) -> None:
        assert self._card is not None
        self.days_table.setRowCount(len(self._card.days))
        for row, d in enumerate(self._card.days):
            weekday_pl = WEEKDAY_PL[d.day.weekday()]
            badges = self._day_badges(d)
            vals = [
                f"{d.day.strftime('%d.%m')} ({weekday_pl})",
                ", ".join(badges) if badges else "—",
                self._countries_by_day.get(d.day, "—"),
                format_clock_local(d.day, d.start_min, self._zone),
                format_clock_local(d.day, d.end_min, self._zone),
                format_minutes(d.work_min),
                format_minutes(d.drive_min),
                format_minutes(d.other_work_min),
                format_minutes(d.duty_50_min),
                format_minutes(d.night_min),
                format_minutes(d.overtime_min),
                d.absence_label,
            ]
            bg = None
            if d.absence_label:
                bg = _COLOR_ABSENCE
            elif "Wn" in badges:
                bg = _COLOR_WN
            elif d.is_manual:
                bg = _COLOR_MANUAL
            elif d.work_min > 0 and not self._countries_by_day.get(d.day, "").strip():
                bg = _COLOR_NO_COUNTRY
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, d.day.isoformat())
                if bg is not None:
                    item.setBackground(bg)
                self.days_table.setItem(row, col, item)
        self.days_table.resizeColumnsToContents()

    def _fill_calendar(self) -> None:
        assert self._card is not None
        stats: dict[date, dict] = {}
        for d in self._card.days:
            sunday_work = d.day.weekday() == 6 and d.work_min > 0
            if d.sunday_holiday_min > 0 and d.work_min > 0:
                sunday_work = True
            stats[d.day] = {
                "work_min": d.work_min,
                "absence": bool(d.absence_label),
                "sunday_work": sunday_work,
                "manual": d.is_manual,
            }
        self.month_calendar.set_month(self._card.year, self._card.month, stats)

    def edit_selected_day(self) -> None:
        if self._card is None:
            QMessageBox.information(self, _tr("Edytuj dzień"), _tr("Najpierw wczytaj miesiąc."))
            return
        did = self.current_driver_id()
        if did is None:
            return
        day = self._selected_day or self.day_inspector.selected_day
        if day is None:
            row = self.days_table.currentRow()
            if row >= 0:
                item = self.days_table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole):
                    day = date.fromisoformat(str(item.data(Qt.ItemDataRole.UserRole)))
        if day is None:
            QMessageBox.information(self, _tr("Edytuj dzień"), _tr("Zaznacz dzień na liście lub w kalendarzu."))
            return
        day_segs = [s for s in self._segments if s.day == day]
        has_override = day in self._manual_days
        dlg = DayEditorDialog(
            self,
            day=day,
            segments=day_segs,
            zone=self._zone,
            has_override=has_override,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.action() == DayEditorDialog.RESULT_RESTORE:
            self.store.clear_day_override(did, day)
        elif dlg.action() == DayEditorDialog.RESULT_SAVE:
            self.store.set_day_override(did, day, dlg.result_override_dicts())
        else:
            return
        self.load_month(silent=True)
        self._select_day(day)

    def _fill_absences(self, absences) -> None:
        self.abs_table.setRowCount(len(absences))
        for row, a in enumerate(absences):
            self.abs_table.setItem(row, 0, QTableWidgetItem(a.day.isoformat()))
            self.abs_table.setItem(
                row, 1, QTableWidgetItem(ABSENCE_LABELS_PL.get(a.kind, a.kind.value))
            )
            self.abs_table.setItem(row, 2, QTableWidgetItem(a.title))
            self.abs_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, a.id)

    def _sync_wn_entries(self) -> None:
        assert self._card is not None
        did = self.current_driver_id()
        assert did is not None
        year, month = self._card.year, self._card.month
        existing = self.store.list_comp_entries(did, year, month)
        for entry in ensure_open_wn_entries(
            existing,
            detect_holiday_wn_debts(self._card),
            driver_id=did,
        ):
            self.store.upsert_comp_entry(entry)
        self._comp_entries = self.store.list_comp_entries(did, year, month)

    def _ordered_milo_stays(
        self, stays, segments, crossings, places
    ):
        """Sort MiLo rows by day, then by first presence from places/crossings."""
        derived = derive_country_minutes(segments, crossings, places)
        return sorted_milo_stays(
            stays, first_seen=presence_first_seen_map(derived)
        )

    def _auto_fill_milo_from_borders(
        self, driver_id: int, segments, crossings, places=None
    ) -> None:
        places = places or []
        derived = derive_country_minutes(segments, crossings, places)
        if places:
            self._countries_by_day = country_labels_from_places(places)
        else:
            by_day: dict[date, list[str]] = {}
            for item in derived:
                code = item.country_code.upper()
                seen = by_day.setdefault(item.day, [])
                if code not in seen:
                    seen.append(code)
            self._countries_by_day = {
                day: ", ".join(codes) for day, codes in sorted(by_day.items())
            }
        if not derived:
            return
        year = self.year_spin.value()
        month = self.month_spin.value()
        existing = self.store.list_milo_stays(driver_id, year, month)
        have = {(e.day, e.country_code.upper()) for e in existing}
        source_note = (
            "auto z GNSS"
            if any(getattr(c, "source", "") == "gnss" for c in crossings)
            else ("auto z border_crossings" if crossings else "auto z places (DDD)")
        )
        for item in derived:
            key = (item.day, item.country_code.upper())
            if key in have:
                continue
            if item.day.year != year or item.day.month != month:
                continue
            op = OpType.BILATERAL
            note = source_note
            disp = classify_stay_from_dispatcher(
                item.day, item.country_code, self._dispatcher_legs
            )
            if disp is not None:
                op = disp.op
                note = f"{source_note} · {disp.reason}"
            self.store.upsert_milo_stay(
                MiloStay(
                    id=None,
                    driver_id=driver_id,
                    day=item.day,
                    country_code=item.country_code,
                    minutes=item.minutes,
                    op_type=op,
                    note=note,
                )
            )

    def _fill_comp(self) -> None:
        self.comp_table.setRowCount(len(self._comp_entries))
        for row, e in enumerate(self._comp_entries):
            self.comp_table.setItem(
                row, 0, QTableWidgetItem(e.earn_day.strftime("%d.%m.%Y"))
            )
            self.comp_table.setItem(
                row, 1, QTableWidgetItem(COMP_LABELS_PL.get(e.kind, e.kind.value))
            )
            self.comp_table.setItem(row, 2, QTableWidgetItem(e.title))
            self.comp_table.setItem(row, 3, QTableWidgetItem(entry_status_pl(e)))
            self.comp_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, e.id)
        self.comp_table.resizeColumnsToContents()

    def _selected_comp_entry(self) -> CompEntry | None:
        row = self.comp_table.currentRow()
        if row < 0:
            return None
        item = self.comp_table.item(row, 0)
        if not item:
            return None
        eid = item.data(Qt.ItemDataRole.UserRole)
        if eid is None:
            return None
        for e in self._comp_entries:
            if e.id == int(eid):
                return e
        return None

    def settle_wn(self) -> None:
        entry = self._selected_comp_entry()
        if entry is None:
            QMessageBox.information(self, "Wn", _tr("Wybierz wpis długu do oddania."))
            return
        if entry.kind != CompKind.SUNDAY_HOLIDAY_WN:
            QMessageBox.information(self, "Wn", _tr("Oddawanie dnia dotyczy wpisów Wn."))
            return
        default = date(self.year_spin.value(), self.month_spin.value(), 1)
        dlg = SettleWnDialog(self, default_day=default)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        entry.settle_day = dlg.settle_day()
        entry.paid_out = False
        self.store.upsert_comp_entry(entry)
        self.load_month(silent=True)

    def pay_wn_100(self) -> None:
        entry = self._selected_comp_entry()
        if entry is None:
            QMessageBox.information(self, "Wn", _tr("Wybierz wpis do wypłaty 100%."))
            return
        if entry.kind != CompKind.SUNDAY_HOLIDAY_WN:
            QMessageBox.information(self, "Wn", _tr("Wypłata 100% dotyczy wpisów Wn."))
            return
        if (
            QMessageBox.question(
                self,
                _tr("Wypłata 100%"),
                f"{_tr('Wypłacić dodatek 100% za pracę w dniu ')}{entry.earn_day.strftime('%d.%m.%Y')} (bez oddania dnia wolnego)?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        entry.paid_out = True
        entry.settle_day = None
        self.store.upsert_comp_entry(entry)
        self.load_month(silent=True)

    def delete_comp_entry(self) -> None:
        entry = self._selected_comp_entry()
        if entry is None or entry.id is None:
            return
        if (
            QMessageBox.question(self, _tr("Usuń"), _tr("Usunąć wpis oddawania?"))
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.store.delete_comp_entry(entry.id)
        self.load_month(silent=True)

    def _suggest_milo_ops(self):
        return suggest_milo_op_types(
            self._milo_stays, dispatcher_legs=self._dispatcher_legs
        )

    def _fill_milo(self) -> None:
        self._milo_stays = sorted_milo_stays(self._milo_stays)
        by_id = {
            s.stay_id: s for s in self._suggest_milo_ops()
        }
        suggest_color = QColor(T.SUGGEST)
        self.milo_table.setRowCount(len(self._milo_stays))
        for row, s in enumerate(self._milo_stays):
            self.milo_table.setItem(
                row, 0, QTableWidgetItem(s.day.strftime("%d.%m.%Y"))
            )
            self.milo_table.setItem(row, 1, QTableWidgetItem(s.country_code))
            self.milo_table.setItem(
                row, 2, QTableWidgetItem(OP_LABELS_PL.get(s.op_type, s.op_type.value))
            )
            self.milo_table.setItem(row, 3, QTableWidgetItem(format_minutes(s.minutes)))
            sug = by_id.get(s.id) if s.id is not None else None
            hint = format_suggestion_hint(sug) if sug is not None else ""
            hint_item = QTableWidgetItem(hint)
            if hint:
                hint_item.setForeground(suggest_color)
            self.milo_table.setItem(row, 4, hint_item)
            self.milo_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, s.id)
        self.milo_table.resizeColumnsToContents()

    def _sync_milo_fx_spin(self, eur: float, year: int, month: int) -> None:
        if not hasattr(self, "milo_fx_spin"):
            return
        self.milo_fx_spin.blockSignals(True)
        self.milo_fx_spin.setValue(float(eur))
        override = self.store.get_month_fx_eur(year, month)
        self.milo_fx_spin.setToolTip(
            f"{_tr('Nadpisanie miesiąca ')}{year}-{month:02d}"
            if override is not None
            else _tr("Kurs bazowy (brak nadpisania miesiąca)")
        )
        self.milo_fx_spin.blockSignals(False)

    def save_month_fx_eur(self) -> None:
        if self._card is None:
            return
        self.milo_fx_spin.interpretText()
        self.store.set_month_fx_eur(
            self._card.year, self._card.month, self.milo_fx_spin.value()
        )
        self.load_month(silent=True)

    def clear_month_fx_eur(self) -> None:
        if self._card is None:
            return
        self.store.set_month_fx_eur(self._card.year, self._card.month, None)
        self.load_month(silent=True)

    def _milo_posting_only(self) -> bool:
        return self.store.get_setting("milo_topup_mode", "posting") != "country_hours"

    def _on_milo_mode_changed(self) -> None:
        box = getattr(self, "milo_country_hours", None)
        if box is None:
            return
        mode = "country_hours" if box.isChecked() else "posting"
        self.store.set_setting("milo_topup_mode", mode)
        if self._card is not None:
            self.load_month(silent=True)

    def _selected_milo_stays(self) -> list[MiloStay]:
        rows = {i.row() for i in self.milo_table.selectedIndexes()}
        by_id = {s.id: s for s in self._milo_stays if s.id is not None}
        out: list[MiloStay] = []
        for row in sorted(rows):
            item = self.milo_table.item(row, 0)
            if not item:
                continue
            sid = item.data(Qt.ItemDataRole.UserRole)
            if sid is None:
                continue
            stay = by_id.get(int(sid))
            if stay is not None:
                out.append(stay)
        return out

    def add_milo_stay(self) -> None:
        did = self.current_driver_id()
        if did is None:
            return
        default = self._selected_day or date(
            self.year_spin.value(), self.month_spin.value(), 1
        )
        dlg = MiloStayDialog(self, store=self.store, default_day=default)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.upsert_milo_stay(dlg.result_stay(did))
        self.load_month(silent=True)

    def edit_milo_stay(self) -> None:
        did = self.current_driver_id()
        if did is None:
            return
        stays = self._selected_milo_stays()
        if len(stays) != 1:
            QMessageBox.information(self, "MiLo", _tr("Wybierz jeden wpis do edycji."))
            return
        stay = stays[0]
        dlg = MiloStayDialog(
            self,
            store=self.store,
            stay=stay,
            suggestion=suggestion_for_stay(
                self._milo_stays,
                stay.id,
                dispatcher_legs=self._dispatcher_legs,
            ),
            dispatcher_hint=dispatcher_hint_for_stay(
                stay.day, stay.country_code, self._dispatcher_legs
            ),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.upsert_milo_stay(dlg.result_stay(did))
        self.load_month(silent=True)

    def delete_milo_stay(self) -> None:
        stays = self._selected_milo_stays()
        if not stays:
            return
        if (
            QMessageBox.question(
                self, _tr("Usuń"), f"{_tr('Usunąć zaznaczone wpisy MiLo (')}{len(stays)})?"
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        for s in stays:
            if s.id is not None:
                self.store.delete_milo_stay(int(s.id))
        self.load_month(silent=True)

    def bulk_set_milo_type(self) -> None:
        stays = self._selected_milo_stays()
        if not stays:
            QMessageBox.information(self, "MiLo", _tr("Zaznacz wiersze do zmiany typu."))
            return
        op = pick_op_type(self, title=_tr("Ustaw typ…"))
        if op is None:
            return
        for s in stays:
            if s.op_type == op:
                continue
            s.op_type = op
            s.note = mark_note_manual(s.note)
            self.store.upsert_milo_stay(s)
        self.load_month(silent=True)

    def bulk_set_auto_milo_type(self) -> None:
        autos = [
            s
            for s in self._milo_stays
            if s.op_type == OpType.BILATERAL and "auto" in (s.note or "").lower()
        ]
        if not autos:
            QMessageBox.information(
                self, "MiLo", _tr("Brak wpisów auto-bilateralnych w tym miesiącu.")
            )
            return
        op = pick_op_type(self, title=_tr("Oznacz wszystkie auto jako…"))
        if op is None:
            return
        for s in autos:
            s.op_type = op
            s.note = mark_note_manual(s.note)
            self.store.upsert_milo_stay(s)
        self.load_month(silent=True)

    def apply_milo_suggestions(self) -> None:
        suggestions = self._suggest_milo_ops()
        if not suggestions:
            QMessageBox.information(
                self, "MiLo", _tr("Brak sugestii zmian typów dla bieżących wpisów.")
            )
            return
        dlg = MiloSuggestionsDialog(self, suggestions=suggestions)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        by_id = {s.id: s for s in self._milo_stays if s.id is not None}
        for sug in dlg.suggestions():
            stay = by_id.get(sug.stay_id)
            if stay is None:
                continue
            stay.op_type = sug.suggested
            stay.note = mark_note_manual(stay.note)
            self.store.upsert_milo_stay(stay)
        self.load_month(silent=True)

    def open_milo_settings(self) -> None:
        MiloSettingsDialog(self, store=self.store).exec()
        if self._card is not None:
            self.load_month(silent=True)

    def _fill_attachments(self) -> None:
        did = self.current_driver_id()
        if did is None:
            return
        atts = self.store.list_attachments(
            did, self.year_spin.value(), self.month_spin.value()
        )
        self.att_table.setRowCount(len(atts))
        for row, a in enumerate(atts):
            self.att_table.setItem(row, 0, QTableWidgetItem(a.filename))
            self.att_table.setItem(row, 1, QTableWidgetItem(a.note))
            self.att_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, a.id)

    def add_absence(self) -> None:
        did = self.current_driver_id()
        if did is None:
            return
        default = self._selected_day or date(
            self.year_spin.value(), self.month_spin.value(), 1
        )
        dlg = AbsenceDialog(self, default_day=default)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.store.upsert_absence(dlg.result_absence(did))
        self.load_month(silent=True)

    def delete_absence(self) -> None:
        row = self.abs_table.currentRow()
        if row < 0:
            return
        item = self.abs_table.item(row, 0)
        if not item:
            return
        abs_id = item.data(Qt.ItemDataRole.UserRole)
        if abs_id is not None:
            self.store.delete_absence(int(abs_id))
            self.load_month(silent=True)

    def add_attachment(self) -> None:
        did = self.current_driver_id()
        if did is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("Załącznik"),
            "",
            _tr("Dokumenty (*.pdf *.jpg *.jpeg *.png);;All (*.*)"),
        )
        if not path:
            return
        note, ok = QInputDialog.getText(self, _tr("Załącznik"), _tr("Uwagi (opcjonalnie):"))
        if not ok:
            note = ""
        self.store.add_attachment(
            did, self.year_spin.value(), self.month_spin.value(), Path(path), note
        )
        self._fill_attachments()
        if self._card is not None:
            self._update_checklist(
                segments_count=len(self._segments),
                pay_work=sum(d.work_min for d in self._card.days),
            )

    def delete_attachment(self) -> None:
        row = self.att_table.currentRow()
        if row < 0:
            return
        item = self.att_table.item(row, 0)
        if not item:
            return
        att_id = item.data(Qt.ItemDataRole.UserRole)
        if att_id is not None:
            self.store.delete_attachment(int(att_id))
            self._fill_attachments()
            if self._card is not None:
                self._update_checklist(
                    segments_count=len(self._segments),
                    pay_work=sum(d.work_min for d in self._card.days),
                )

    def close_month(self) -> None:
        if self._card is None:
            self.load_month(silent=True)
        if self._card is None:
            return
        dlg = CloseMonthDialog(self, self._last_checklist)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.export_pdf()

    def export_pdf(self) -> None:
        if self._card is None:
            self.load_month(silent=True)
        if self._card is None:
            return
        did = self.current_driver_id()
        assert did is not None
        atts = self.store.list_attachments(did, self._card.year, self._card.month)
        comps = self.store.list_comp_entries(did, self._card.year, self._card.month)
        stays = list(self._milo_stays) if self._milo_stays else self.store.list_milo_stays(
            did, self._card.year, self._card.month
        )
        sunday_u = unreturned_sunday_holiday_minutes(self._card, comps)
        pay_base = compute_payroll(self._card, sunday_unreturned_min=sunday_u)
        driver = self.store.get_driver(did)
        fx = self.store.get_fx_pln(year=self._card.year, month=self._card.month)
        milo = compute_milo_topup(
            stays,
            pl_hourly_pln=pay_base.hourly_rate,
            rates=self.store.list_country_rates(),
            fx_pln=fx,
            at_band=driver.at_seniority if driver else AtSeniority.A,
            posting_only=self._milo_posting_only(),
        )
        pay = compute_payroll(
            self._card,
            sunday_unreturned_min=sunday_u,
            milo_topup_pln=milo.topup_total_pln,
        )
        diet = compute_virtual_diet(
            stays,
            eur_pln=float(fx.get("EUR", 4.30)),
            avg_wage_pln=self.store.get_avg_wage_pln(),
        )
        suggested = str(default_report_path(self._card))
        path, _ = QFileDialog.getSaveFileName(
            self,
            _tr("Zapisz PDF ewidencji"),
            suggested,
            "PDF (*.pdf)",
        )
        if not path:
            return
        out = export_month_pdf(
            self._card,
            Path(path),
            attachments=atts,
            payroll=pay,
            comp_entries=comps,
            sunday_unreturned_min=sunday_u,
            milo_stays=stays,
            milo_result=milo,
            milo_topup_pln=milo.topup_total_pln,
            virtual_diet=diet,
            zone=self._zone,
        )
        self.store.save_month_closure(
            did,
            self._card.year,
            self._card.month,
            {"pdf": str(out), "driver": self._card.driver.full_name},
        )
        self._pdf_done_for = (did, self._card.year, self._card.month)
        self._update_checklist(
            segments_count=len(self._segments),
            pay_work=pay.work_min,
        )
        QMessageBox.information(
            self,
            "PDF",
            f"{_tr('Zapisano ewidencję:\n')}{out}\n\nFolder kierowcy: {driver_reports_dir(self._card.driver.full_name)}\nWszystkie raporty: {reports_dir()}",
        )
