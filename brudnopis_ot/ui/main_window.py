from __future__ import annotations

from spedi.i18n import tr as _tr

import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QDate, QSettings, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QInputDialog,
    QPushButton,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from brudnopis_ot.core.calendar_weeks import ensure_monday
from brudnopis_ot.core.daily_summary import totals_for_visible_scenario
from brudnopis_ot.core.ddd_blocks import scenario_from_segments
from brudnopis_ot.core.ddd_file_loader import (
    DddLoadError,
    DddLoadResult,
    load_ddd_batch,
    merge_batch_into_session,
)
from brudnopis_ot.core.ddd_store import DddProfileStore, DddProfileSummary
from brudnopis_ot.core.display_tz import (
    DEFAULT_ZONE,
    SETTINGS_APP,
    SETTINGS_KEY,
    SETTINGS_ORG,
    normalize_zone,
    zone_label,
)
from brudnopis_ot.core.models import ActivityKind, Block, Scenario, copy_scenario
from brudnopis_ot.core.paths import scenarios_dir, safe_filename_part
from brudnopis_ot.core.serialize import load_scenario, save_scenario
from brudnopis_ot.core.validator import validate_scenario
from brudnopis_ot.ui.block_editor import BlockEditorDialog
from brudnopis_ot.ui.cycle_start_dialog import CycleStartDialog
from brudnopis_ot.ui.daily_summary_panel import DailySummaryPanel
from brudnopis_ot.ui.report_dialog import ReportPeriodDialog
from brudnopis_ot.ui.ddd_profile_dialog import DddProfilePickDialog
from brudnopis_ot.ui.findings_panel import FindingsPanel
from brudnopis_ot.ui.legend_widget import LegendWidget
from brudnopis_ot.ui.week_grid import WeekGridWidget
from brudnopis_ot.core.paths import scenarios_dir, safe_filename_part
from brudnopis_ot.core.serialize import load_scenario, save_scenario
from brudnopis_ot.core.validator import validate_scenario
from brudnopis_ot.ui.block_editor import BlockEditorDialog
from brudnopis_ot.ui.daily_summary_panel import DailySummaryPanel
from brudnopis_ot.ui.report_dialog import ReportPeriodDialog
from brudnopis_ot.ui.ddd_profile_dialog import DddProfilePickDialog
from brudnopis_ot.ui.findings_panel import FindingsPanel
from brudnopis_ot.ui.legend_widget import LegendWidget
from brudnopis_ot.ui.week_grid import WeekGridWidget

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _examples_dir() -> Path:
    """Resolve examples both in source tree and PyInstaller bundle."""
    candidates = [
        _EXAMPLES_DIR,
        Path(getattr(sys, "_MEIPASS", "")) / "brudnopis_ot" / "examples",
        Path(sys.executable).resolve().parent / "brudnopis_ot" / "examples",
        Path(sys.executable).resolve().parent / "_internal" / "brudnopis_ot" / "examples",
    ]
    for path in candidates:
        if path and str(path) != "." and path.exists():
            return path
    return _EXAMPLES_DIR


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Brudnopis OT — odpoczynki tygodniowe 561/2006"))
        self.resize(1280, 720)
        if parent is not None:
            self.setWindowFlags(Qt.WindowType.Widget)

        self._path: Path | None = None
        self._scenario = Scenario(
            week_start=ensure_monday(date.today()),
            weeks=5,
            title=_tr("Nowy scenariusz"),
        )
        self._dirty = False
        self._ddd_profile: DddLoadResult | None = None
        self._cycle_start: date | None = None
        self._template_source: Scenario | None = None
        self._template_view_blocks: dict[str, Block] = {}
        self._ddd_modified = True
        self._ddd_source: Scenario | None = None
        self._ddd_view_blocks: dict[str, Block] = {}
        self._ddd_store = DddProfileStore()
        try:
            self._ddd_session: list[DddProfileSummary | DddLoadResult] = self._ddd_store.list_profile_summaries()
        except Exception:  # noqa: BLE001
            self._ddd_session = []
        self._ddd_combo: QComboBox | None = None
        self._updating_ddd_combo = False
        self._updating_cycle = False
        self._zone = normalize_zone(
            QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_KEY, DEFAULT_ZONE)
        )

        self.grid = WeekGridWidget()
        self.findings = FindingsPanel()
        self.daily = DailySummaryPanel()
        self.legend = LegendWidget()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        zoom_row = QHBoxLayout()
        zoom_lbl = QLabel(_tr("Rozciągnięcie osi"))
        zoom_lbl.setObjectName("SectionLabel")
        self._zoom_out_btn = QToolButton()
        self._zoom_out_btn.setObjectName("GhostButton")
        self._zoom_out_btn.setText("−")
        self._zoom_out_btn.setToolTip(_tr("Zwęź oś czasu"))
        self._zoom_out_btn.clicked.connect(self.grid.zoom_out)
        self._zoom_in_btn = QToolButton()
        self._zoom_in_btn.setObjectName("GhostButton")
        self._zoom_in_btn.setText("+")
        self._zoom_in_btn.setToolTip(_tr("Rozciągnij oś czasu"))
        self._zoom_in_btn.clicked.connect(self.grid.zoom_in)
        zoom_row.addWidget(zoom_lbl)
        zoom_row.addWidget(self._zoom_out_btn)
        zoom_row.addWidget(self._zoom_in_btn)
        self._lens_btn = QToolButton()
        self._lens_btn.setObjectName("GhostButton")
        self._lens_btn.setText(_tr("Lupa"))
        self._lens_btn.setCheckable(True)
        self._lens_btn.setToolTip(_tr("Zaznacz prostokątem fragment osi, aby powiększyć i wybrać blok. Esc anuluje."))
        self._lens_btn.toggled.connect(self.grid.set_lens_mode)
        self.grid.lens_mode_changed.connect(self._lens_btn.setChecked)
        zoom_row.addWidget(self._lens_btn)
        self._ddd_view = QComboBox()
        self._ddd_view.addItems([_tr('Zmodyfikowany'), _tr('Oryginał DDD')])
        self._ddd_view.setToolTip(_tr('Korekty zapisują się automatycznie dla kierowcy. Oryginał DDD służy do porównania.'))
        self._ddd_view.currentIndexChanged.connect(self._on_ddd_view_changed)
        zoom_row.addWidget(self._ddd_view)
        zoom_row.addStretch(1)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.addLayout(zoom_row)
        self._open_activity_row = QWidget()
        pending_layout = QHBoxLayout(self._open_activity_row)
        pending_layout.setContentsMargins(0, 0, 0, 0)
        self._open_activity_label = QLabel()
        self._open_activity_label.setTextFormat(Qt.TextFormat.PlainText)
        self._open_activity_label.setWordWrap(True)
        pending_layout.addWidget(self._open_activity_label, stretch=1)
        self._activity_end_button = QPushButton(_tr('Ustaw koniec…'))
        self._activity_end_button.clicked.connect(self._confirm_open_activity_end)
        pending_layout.addWidget(self._activity_end_button)
        center_layout.addWidget(self._open_activity_row)
        center_layout.addWidget(scroll, stretch=1)
        center_layout.addWidget(self.legend)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.findings)
        right.addWidget(self.daily)
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 1)

        axis = QWidget()
        layout = QHBoxLayout(axis)
        layout.addWidget(center, stretch=1)
        layout.addWidget(right)

        self._analyzer = None
        try:
            from ddd_analyzer.ui.main_window import MainWindow as AnalyzerWindow

            self._analyzer = AnalyzerWindow(self)
        except Exception:  # noqa: BLE001
            self._analyzer = QLabel(_tr("DDD Analyzer niedostępny."))

        tabs = QTabWidget()
        tabs.addTab(axis, _tr("Oś"))
        tabs.addTab(self._analyzer, _tr("Analiza DDD"))
        self._tabs = tabs
        self._template_analysis = QLabel(_tr("Ręczny profil nie ma plików źródłowych DDD. Obliczenia są dostępne w zakładce Oś."))
        self._template_analysis.setWordWrap(True)
        self._template_analysis.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(tabs)

        self._status = QLabel()
        self.statusBar().addWidget(self._status)

        self.grid.block_clicked.connect(self._edit_block)
        self.grid.delete_block_requested.connect(self._delete_block)
        self.grid.empty_clicked.connect(self._new_block_at)
        self.grid.set_week1_anchor.connect(self._set_week1_anchor)
        self.findings.finding_activated.connect(self._on_finding)
        self.daily.day_activated.connect(self._on_day_selected)

        self._build_toolbar()
        self._refresh()

    def _build_toolbar(self) -> None:
        tb = QToolBar(_tr("Plik"))
        tb.setMovable(False)
        self.addToolBar(tb)

        act_new = QAction(_tr("Nowy"), self)
        act_new.setShortcut(QKeySequence.StandardKey.New)
        act_new.triggered.connect(self._new_scenario)
        tb.addAction(act_new)

        act_open = QAction(_tr("Otwórz…"), self)
        act_open.setShortcut(QKeySequence.StandardKey.Open)
        act_open.triggered.connect(self._open)
        tb.addAction(act_open)

        act_save = QAction(_tr("Zapisz"), self)
        act_save.setShortcut(QKeySequence.StandardKey.Save)
        act_save.triggered.connect(self._save)
        tb.addAction(act_save)

        act_save_as = QAction(_tr("Zapisz jako…"), self)
        act_save_as.triggered.connect(self._save_as)
        tb.addAction(act_save_as)

        act_report = QAction(_tr("Raport PDF"), self)
        act_report.setToolTip(_tr("Generuj raport PDF aktywnego profilu za wybrany okres"))
        act_report.triggered.connect(self._export_pdf_report)
        tb.addAction(act_report)

        tb.addSeparator()

        file_tb = tb
        tb = QToolBar(_tr("Profile"), self)
        tb.setMovable(False)
        act_ddd = QAction(_tr("Dodaj pliki DDD…"), self)
        act_ddd.setToolTip(
            _tr("Wiele plików C_* (karta) i M_* (VU) naraz — program sam pogrupuje "
            "po karcie / rejestracji i pozwoli wybrać profil.")
        )
        act_ddd.triggered.connect(self._add_ddd_file)
        tb.addAction(act_ddd)
        act_refresh_ddd = QAction(_tr("Odśwież z archiwum DDD"), self)
        act_refresh_ddd.triggered.connect(self._refresh_ddd_archive)
        tb.addAction(act_refresh_ddd)
        act_end_ddd = QAction(_tr("Sprawdź koniec DDD…"), self)
        act_end_ddd.triggered.connect(self._check_ddd_end)
        tb.addAction(act_end_ddd)

        template_button = QToolButton()
        template_button.setText(_tr("Ręczny profil"))
        template_button.setToolTip(_tr("Template Driver — scenariusz bez pliku DDD"))
        template_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        template_menu = QMenu(template_button)
        template_menu.addAction(_tr("Utwórz…"), self._new_template)
        self._rename_template_action = template_menu.addAction(_tr("Zmień nazwę…"), self._rename_template)
        self._delete_template_action = template_menu.addAction(_tr("Usuń profil…"), self._delete_template)
        template_button.setMenu(template_menu)
        tb.addWidget(template_button)

        tb.addWidget(QLabel(_tr(" Profil: ")))
        self._ddd_combo = QComboBox()
        self._ddd_combo.setMinimumWidth(220)
        self._ddd_combo.setToolTip(_tr("Zapisane profile DDD i ręczne profile Template Driver"))
        self._ddd_combo.currentIndexChanged.connect(self._on_ddd_profile_selected)
        tb.addWidget(self._ddd_combo)
        self._rebuild_ddd_combo()

        tb.addWidget(QLabel(_tr(" Początek cyklu: ")))
        self._cycle_edit = QDateEdit()
        self._cycle_edit.setCalendarPopup(True)
        self._cycle_edit.setDisplayFormat("yyyy-MM-dd")
        self._cycle_edit.setDate(ensure_monday(date.today()))
        self._cycle_edit.setToolTip(
            _tr("Od tej daty (poniedziałek) oś pokazuje cykl. Starsza historia zostaje w bazie.")
        )
        self._cycle_edit.dateChanged.connect(self._on_cycle_date_changed)
        tb.addWidget(self._cycle_edit)

        profile_tb = tb
        tb = file_tb
        tb.addSeparator()

        act_add_week = QAction(_tr("Dodaj tydzień"), self)
        act_add_week.triggered.connect(self._add_week)
        tb.addAction(act_add_week)

        act_remove_last = QAction(_tr("Usuń ostatni tydzień"), self)
        act_remove_last.triggered.connect(self._remove_last_week)
        tb.addAction(act_remove_last)

        act_remove_first = QAction(_tr("Usuń pierwszy tydzień"), self)
        act_remove_first.triggered.connect(self._remove_first_week)
        tb.addAction(act_remove_first)

        act_demo = QAction(_tr("Przykład: film PM"), self)
        act_demo.triggered.connect(self._load_demo)
        tb.addAction(act_demo)

        tb.addSeparator()
        tb.addWidget(QLabel(_tr(" Strefa: ")))
        self._zone_combo = QComboBox()
        self._zone_combo.addItem(zone_label("Europe/Warsaw"), "Europe/Warsaw")
        self._zone_combo.addItem(zone_label("UTC"), "UTC")
        zidx = self._zone_combo.findData(self._zone)
        if zidx >= 0:
            self._zone_combo.setCurrentIndex(zidx)
        self._zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        tb.addWidget(self._zone_combo)

        tb.addSeparator()
        act_zoom_in = QAction(_tr("Oś +"), self)
        act_zoom_in.setToolTip(_tr("Rozciągnij oś czasu"))
        act_zoom_in.setShortcuts(
            [QKeySequence.StandardKey.ZoomIn, QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")]
        )
        act_zoom_in.triggered.connect(self.grid.zoom_in)
        tb.addAction(act_zoom_in)
        act_zoom_out = QAction(_tr("Oś −"), self)
        act_zoom_out.setToolTip(_tr("Zwęź oś czasu"))
        act_zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        act_zoom_out.triggered.connect(self.grid.zoom_out)
        tb.addAction(act_zoom_out)
        self.addAction(act_zoom_in)
        self.addAction(act_zoom_out)
        for btn in tb.findChildren(QToolButton):
            btn.setAutoRaise(False)
        self.addToolBarBreak()
        self.addToolBar(profile_tb)
        for btn in profile_tb.findChildren(QToolButton):
            btn.setAutoRaise(False)

    def _qdate(self, day: date) -> QDate:
        return QDate(day.year, day.month, day.day)

    def _default_cycle_start(self, loaded: DddLoadResult) -> date:
        if loaded.date_to and loaded.date_from:
            span = (loaded.date_to - loaded.date_from).days
            if span > 28:
                return ensure_monday(loaded.date_to - timedelta(days=21))
            return ensure_monday(loaded.date_from)
        return ensure_monday(date.today())

    def _sync_cycle_edit(self) -> None:
        day = self._cycle_start or self._scenario.week_start
        self._updating_cycle = True
        self._cycle_edit.setDate(self._qdate(ensure_monday(day)))
        self._updating_cycle = False

    def _on_cycle_date_changed(self, qday: QDate) -> None:
        if self._updating_cycle:
            return
        chosen = ensure_monday(date(qday.year(), qday.month(), qday.day()))
        self._cycle_start = chosen
        if self._is_template():
            self._set_template_axis(chosen)
            self._mark_dirty()
            self._sync_cycle_edit()
            self._refresh()
            return
        if self._ddd_profile is not None:
            if not self._ddd_modified:
                self._sync_cycle_edit()
                return
            self._ddd_source.week_start = chosen
            self._ddd_source.anchor_block_id = None
            self._project_ddd()
            self._mark_dirty()
            self._path = None
            self.grid.set_selected_ids(set())
            self._refresh()
        else:
            self._scenario.set_axis_monday(chosen)
            self._refresh()
        self._sync_cycle_edit()

    def _sync_analyzer(self, loaded: DddLoadResult) -> None:
        manual = loaded.source_kind == "template"
        widget = self._template_analysis if manual else self._analyzer
        if self._tabs.widget(1) is not widget:
            self._tabs.removeTab(1)
            self._tabs.insertTab(1, widget, _tr("Analiza DDD"))
        if manual:
            return
        analyzer = self._analyzer
        if analyzer is not None and hasattr(analyzer, "load_profile"):
            analyzer.load_profile(loaded)
            return
        if analyzer is None or not hasattr(analyzer, "load_paths"):
            return
        paths = self._ddd_store.list_archived_files(loaded.profile_key)
        if paths:
            analyzer.load_paths([str(p) for p in paths])

    def _confirm_overwrite(self, source: str = "import") -> bool:
        if not self._dirty:
            return True
        reply = QMessageBox.question(
            self,
            _tr("Nadpisać brudnopis?"),
            f"{_tr('Masz niezapisane ręczne zmiany bloków.\nWczytanie (')}{source}{_tr(') je zastąpi.\n\nKontynuować?')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _rebuild_ddd_combo(self) -> None:
        if self._ddd_combo is None:
            return
        self._updating_ddd_combo = True
        current = self._ddd_profile.profile_key if self._ddd_profile else None
        self._ddd_combo.clear()
        self._ddd_combo.addItem(_tr("— wybierz profil —"), None)
        for profile in self._ddd_session:
            kind = "Template" if profile.source_kind == "template" else ("VU" if profile.source_kind == "vu" else _tr("Karta"))
            label = f"[{kind}] {profile.profile_label}"
            if profile.date_from and profile.date_to:
                label += (
                    f" · {profile.date_from.strftime('%d.%m')}–"
                    f"{profile.date_to.strftime('%d.%m')}"
                )
            self._ddd_combo.addItem(label, profile.profile_key)
        if current:
            idx = self._ddd_combo.findData(current)
            if idx >= 0:
                self._ddd_combo.setCurrentIndex(idx)
        self._updating_ddd_combo = False

    def _apply_ddd_profile(
        self, loaded: DddLoadResult, *, ask_cycle: bool = False
    ) -> None:
        from brudnopis_ot.core.ddd_import import recover_open_activity
        loaded = recover_open_activity(self._ddd_store, loaded.profile_key) or loaded
        stored = self._ddd_store.get_cycle_start(loaded.profile_key)
        suggested = stored or self._default_cycle_start(loaded)
        if ask_cycle:
            dlg = CycleStartDialog(
                self,
                suggested=suggested,
                earliest=loaded.date_from,
                latest=loaded.date_to,
            )
            if dlg.exec() == dlg.DialogCode.Accepted:
                cycle_start = dlg.selected_monday()
            else:
                cycle_start = suggested
            dlg.deleteLater()
        else:
            cycle_start = suggested
        if loaded.source_kind == "template":
            source = self._ddd_store.load_template_scenario(loaded.profile_key)
        else:
            if len(loaded.segments) > 5000:
                from spedi.background import run_background
                store_path, store_type = self._ddd_store.path, type(self._ddd_store)
                key, original = loaded.profile_key, not self._ddd_modified
                source = run_background(self, _tr('Wczytywanie historii kierowcy…'),
                    lambda task: store_type(store_path).load_ddd_scenario(key, original=original))
            else:
                source = self._ddd_store.load_ddd_scenario(loaded.profile_key, original=not self._ddd_modified)
            if ask_cycle:
                source.week_start = cycle_start
        scenario = copy_scenario(source)
        scenario.set_axis_monday(scenario.week_start)
        view_blocks = {b.id: replace(b) for b in scenario.blocks}
        if ask_cycle:
            self._ddd_store.set_cycle_start(loaded.profile_key, cycle_start)
        # Commit the selected identity together with its successfully loaded timeline.
        # An I/O failure must never leave the previous driver's edits under a new key.
        self._ddd_profile = loaded
        session = [p for p in self._ddd_session if p.profile_key != loaded.profile_key]
        self._ddd_session = sorted([*session, loaded], key=lambda p: (p.source_kind, p.profile_label.casefold()))
        self._scenario = scenario
        self._cycle_start = scenario.week_start
        if loaded.source_kind == 'template':
            self._template_source = source
            self._template_view_blocks = view_blocks
        else:
            self._ddd_source = source
            self._ddd_view_blocks = view_blocks
        self._path = None
        self._dirty = False
        self._rebuild_ddd_combo()
        self._sync_cycle_edit()
        self._sync_analyzer(loaded)
        self.grid.set_selected_ids(set())
        self._refresh()
        if loaded.source_kind == "template":
            self.statusBar().showMessage(f"{loaded.profile_label} · zmiany zapisywane automatycznie", 8000)
            return
        span = ""
        if loaded.date_from and loaded.date_to:
            span = (
                f" · {loaded.date_from.strftime('%d.%m.%Y')}–"
                f"{loaded.date_to.strftime('%d.%m.%Y')}"
            )
        kind = "VU" if loaded.source_kind == "vu" else "karta"
        self.statusBar().showMessage(
            f"DDD ({kind}): {loaded.profile_label} · {loaded.filename}{span} · {len(loaded.segments)}{_tr(' segmentów')}",
            8000,
        )
        if not loaded.segments and loaded.open_activity is None:
            QMessageBox.information(
                self,
                _tr("Plik DDD"),
                f"{_tr('Brak segmentów aktywności w: ')}{loaded.filename}",
            )

    def _confirm_open_activity_end(self) -> None:
        from brudnopis_ot.ui.activity_end_dialog import ActivityEndDialog
        if not self._ddd_profile or self._ddd_read_only() or not self._ddd_profile.open_activity:
            return
        activity = self._ddd_profile.open_activity
        dialog = ActivityEndDialog(activity, zone=self._zone,
            confirmed_end=self._ddd_store.get_confirmed_activity_end(self._ddd_profile.profile_key, activity), parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        try:
            if self._dirty and not self._persist_ddd():
                return
            self._ddd_store.confirm_activity_end(self._ddd_profile.profile_key, activity, dialog.end_utc)
            self._apply_ddd_profile(self._ddd_store.get(self._ddd_profile.profile_key))
        except Exception as exc:
            QMessageBox.warning(self, _tr('Koniec ostatniej aktywności'), str(exc))

    def _refresh_open_activity(self):
        from ddd_analyzer.models import ACTIVITY_LABELS_PL
        from brudnopis_ot.core.display_tz import format_dt
        pending = self._ddd_profile.open_activity if self._ddd_profile else None
        self._open_activity_row.setVisible(pending is not None)
        if pending is None:
            return
        end = self._ddd_store.get_confirmed_activity_end(self._ddd_profile.profile_key, pending) if self._ddd_modified else None
        text = f'{ACTIVITY_LABELS_PL[pending.activity]} · {format_dt(pending.start, self._zone)} · {zone_label(self._zone)}'
        if end:
            minutes = int((end - pending.start).total_seconds() // 60)
            text += f" · {_tr('Potwierdzony koniec: ')}{format_dt(end, self._zone)} ({minutes // 60}:{minutes % 60:02d})"
        else:
            text += ' · ' + _tr('Brak potwierdzonego końca — czas trwania nie jest jeszcze liczony.')
        self._open_activity_label.setText(text)
        self._activity_end_button.setEnabled(self._ddd_modified)

    def _pick_ddd_profile(
        self,
        profiles: list[DddLoadResult],
        *,
        link_hints: tuple[str, ...] = (),
        errors: tuple[str, ...] = (),
    ) -> DddLoadResult | None:
        profiles = [p for p in profiles if p.source_kind != "template"]
        if not profiles:
            return None
        if len(profiles) == 1 and not errors and not link_hints:
            return profiles[0]
        dlg = DddProfilePickDialog(
            profiles,
            link_hints=link_hints,
            errors=errors,
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return None
        return dlg.selected_profile()

    def _add_ddd_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            _tr("Dodaj pliki DDD — karty (C_*) i/lub tachografy VU (M_*)"),
            "",
            _tr("Pliki DDD (*.ddd);;Wszystkie pliki (*.*)"),
        )
        if not paths:
            return
        from brudnopis_ot.core.ddd_import import import_files
        from spedi.background import run_background
        active_key = self._ddd_profile.profile_key if self._ddd_profile else None
        store_path = self._ddd_store.path
        store_type = type(self._ddd_store)
        report = run_background(self, _tr('Importowanie plików DDD…'),
            lambda task: import_files(store_type(store_path), paths,
                cancelled=task.isInterruptionRequested, progress=task.progress.emit), cancellable=True)
        self._ddd_session = self._ddd_store.list_profile_summaries()
        self._rebuild_ddd_combo()
        QMessageBox.information(self, _tr("Podsumowanie importu DDD"), report.text())
        # Preserve edited scenarios and the selected Template Driver.
        if self._dirty or self._is_template():
            return
        chosen = self._ddd_store.get(active_key) if active_key else None
        if chosen is None and len(report.profiles) == 1:
            chosen = next(iter(report.profiles.values()))
        if chosen is not None:
            self._apply_ddd_profile(chosen, ask_cycle=False)

    def _refresh_ddd_archive(self) -> None:
        from brudnopis_ot.core.ddd_import import refresh_profile
        if self._ddd_profile is None or self._is_template():
            QMessageBox.information(self, _tr("Archiwum DDD"), _tr("Wybierz profil zaimportowany z DDD."))
            return
        if self._dirty:
            QMessageBox.warning(self, _tr("Archiwum DDD"), _tr("Scenariusz ma ręczne korekty. Zapisz je do osobnego pliku przed odświeżeniem. Dane pozostają bez zmian."))
            return
        try:
            from spedi.background import run_background
            store_path, key = self._ddd_store.path, self._ddd_profile.profile_key
            store_type = type(self._ddd_store)
            saved = run_background(self, _tr('Przeliczanie archiwum DDD…'),
                lambda task: refresh_profile(store_type(store_path), key))
        except Exception as exc:
            QMessageBox.warning(self, _tr("Nie przeliczono profilu"), str(exc))
            return
        self._apply_ddd_profile(saved)
        QMessageBox.information(self, _tr("Archiwum DDD"), _tr("Przeliczono wybrany profil z jego zweryfikowanych źródeł DDD."))

    def _check_ddd_end(self) -> None:
        from brudnopis_ot.core.ddd_import import preview_profile_end, apply_profile_end
        if self._ddd_profile is None or self._is_template():
            QMessageBox.information(self, _tr("Koniec DDD"), _tr("Wybierz profil zaimportowany z DDD."))
            return
        if self._dirty:
            QMessageBox.warning(self, _tr("Koniec DDD"), _tr("Najpierw zapisz ręczne korekty scenariusza. Profil nie został zmieniony."))
            return
        path, _ = QFileDialog.getOpenFileName(self, _tr("Wybierz najnowszy plik DDD tego kierowcy"), "", _tr("Pliki DDD (*.ddd)"))
        if not path:
            return
        try:
            old, candidate, cutoff, minutes = preview_profile_end(self._ddd_store, self._ddd_profile.profile_key, path)
            if not minutes:
                QMessageBox.information(self, _tr("Koniec DDD"), _tr("Profil nie zawiera aktywności wykraczających poza potwierdzony zakres tego pliku."))
                return
            text = (f"{_tr('Potwierdzony zakres aktywności kończy się: ')}{cutoff:%d.%m.%Y %H:%M} UTC.\nPo tym momencie profil zawiera {minutes}{_tr(' min aktywności.\n\nUsunięte zostaną wszystkie minuty po tej granicy, również ewentualne ręczne wpisy lub dane z nowszych plików. Kontynuuj tylko, jeśli wskazany plik ma wyznaczać koniec tego rozliczenia.\n\nProgram zapisze kopię wcześniejszego profilu w bazie. Pliki DDD pozostaną bez zmian.\nCzy zastosować tę korektę?')}")
            if QMessageBox.question(self, _tr("Korekta końca profilu"), text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            saved = apply_profile_end(self._ddd_store, old, candidate)
            self._apply_ddd_profile(saved, ask_cycle=False)
        except Exception as exc:
            QMessageBox.warning(self, _tr("Nie zmieniono końca profilu"), str(exc))

    def _on_ddd_profile_selected(self, index: int) -> None:
        if self._updating_ddd_combo or self._ddd_combo is None:
            return
        key = self._ddd_combo.itemData(index)
        if key is None:
            return
        profile = next((p for p in self._ddd_session if p.profile_key == key), None)
        if profile is None:
            return
        if self._ddd_profile and self._ddd_profile.profile_key == key:
            return
        if self._dirty and not self._confirm_overwrite("profil DDD"):
            self._updating_ddd_combo = True
            if self._ddd_profile:
                idx = self._ddd_combo.findData(self._ddd_profile.profile_key)
                self._ddd_combo.setCurrentIndex(max(0, idx))
            else:
                self._ddd_combo.setCurrentIndex(0)
            self._updating_ddd_combo = False
            return
        try:
            loaded = self._ddd_store.get(key)
            if loaded is not None:
                self._apply_ddd_profile(loaded)
        except Exception as exc:
            self._rebuild_ddd_combo()
            QMessageBox.warning(self, _tr('Nie można wczytać profilu'), str(exc))

    def _is_template(self) -> bool:
        return self._ddd_profile is not None and self._ddd_profile.source_kind == "template"

    def _ddd_read_only(self) -> bool:
        return self._ddd_profile is not None and not self._is_template() and not self._ddd_modified

    def _project_ddd(self) -> None:
        self._scenario = copy_scenario(self._ddd_source)
        self._scenario.set_axis_monday(self._scenario.week_start)
        self._ddd_view_blocks = {b.id: replace(b) for b in self._scenario.blocks}

    def _on_ddd_view_changed(self, index: int) -> None:
        if self._dirty and not self._is_template() and self._ddd_profile:
            if not self._persist_ddd():
                self._ddd_view.blockSignals(True)
                self._ddd_view.setCurrentIndex(0 if self._ddd_modified else 1)
                self._ddd_view.blockSignals(False)
                return
        previous = self._ddd_modified
        self._ddd_modified = index == 0
        if self._ddd_profile and not self._is_template():
            try:
                self._apply_ddd_profile(self._ddd_store.get(self._ddd_profile.profile_key))
            except Exception as exc:
                self._ddd_modified = previous
                self._ddd_view.blockSignals(True)
                self._ddd_view.setCurrentIndex(0 if previous else 1)
                self._ddd_view.blockSignals(False)
                QMessageBox.warning(self, _tr('Nie można wczytać profilu'), str(exc))

    def _persist_ddd(self) -> bool:
        from brudnopis_ot.core.profile_scenario import update_full_scenario
        if self._ddd_read_only():
            return True
        try:
            full = update_full_scenario(self._ddd_source, self._ddd_view_blocks, self._scenario)
            if len(full.blocks) > 5000:
                from spedi.background import run_background
                store_path, store_type, key = self._ddd_store.path, type(self._ddd_store), self._ddd_profile.profile_key
                run_background(self, _tr('Zapisywanie zmian…'),
                    lambda task: store_type(store_path).save_ddd_scenario(key, full))
            else:
                self._ddd_store.save_ddd_scenario(self._ddd_profile.profile_key, full)
            self._ddd_source = copy_scenario(full)
            self._ddd_view_blocks = {b.id: replace(b) for b in self._scenario.blocks}
            self._cycle_start = self._scenario.week_start
            self._sync_cycle_edit()
            self._dirty = False
            return True
        except Exception as exc:
            self._dirty = True
            QMessageBox.critical(self, _tr('Korekty DDD'), f"{_tr('Nie udało się zapisać zmian:\n')}{exc}")
            return False

    def _project_template(self) -> None:
        # Use the same axis clipping as DDD, keeping the full editable history in SQLite.
        self._scenario = copy_scenario(self._template_source)
        self._scenario.set_axis_monday(self._scenario.week_start)
        self._template_view_blocks = {b.id: replace(b) for b in self._scenario.blocks}

    def _set_template_axis(self, chosen: date, anchor_id: str | None = None) -> None:
        self._template_source.week_start = chosen
        self._template_source.anchor_block_id = anchor_id
        self._project_template()

    def _new_template(self) -> None:
        if not self._confirm_overwrite(_tr("ręczny profil")):
            return
        name, ok = QInputDialog.getText(self, "Template Driver", _tr("Nazwa kierowcy / profilu:"))
        if not ok:
            return
        try:
            profile = self._ddd_store.create_template(name, self._scenario.week_start)
            self._apply_ddd_profile(profile)
        except Exception as exc:
            QMessageBox.critical(self, _tr("Ręczny profil"), str(exc))

    def _rename_template(self) -> None:
        if not self._is_template():
            return
        name, ok = QInputDialog.getText(self, _tr("Ręczny profil"), _tr("Nazwa kierowcy / profilu:"), text=self._scenario.title)
        if not ok:
            return
        if not name.strip():
            QMessageBox.information(self, _tr("Ręczny profil"), _tr("Podaj nazwę profilu."))
            return
        self._scenario.title = name.strip()
        self._mark_dirty()
        self._refresh()

    def _delete_template(self) -> None:
        if not self._is_template():
            return
        if QMessageBox.question(self, _tr("Usuń ręczny profil"), f"{_tr('Usunąć profil ')}{self._scenario.title} i wszystkie jego bloki?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._ddd_store.delete_template(self._ddd_profile.profile_key)
        except Exception as exc:
            QMessageBox.critical(self, _tr("Ręczny profil"), str(exc))
            return
        self._new_scenario()

    def _persist_template(self) -> bool:
        try:
            source_blocks = {b.id: b for b in self._template_source.blocks}
            current = {b.id: b for b in self._scenario.blocks}
            for bid in self._template_view_blocks.keys() - current.keys():
                source_blocks.pop(bid, None)
            for bid, block in current.items():
                if self._template_view_blocks.get(bid) != block:
                    source_blocks[bid] = replace(block)
            full = replace(self._scenario, blocks=list(source_blocks.values()))
            self._ddd_store.save_template_scenario(self._ddd_profile.profile_key, full)
            self._template_source = copy_scenario(full)
            self._template_view_blocks = {b.id: replace(b) for b in self._scenario.blocks}
            self._ddd_profile = self._ddd_store.get(self._ddd_profile.profile_key)
            self._ddd_session = self._ddd_store.list_profile_summaries()
            self._cycle_start = self._scenario.week_start
            self._sync_cycle_edit()
            self._rebuild_ddd_combo()
            self._dirty = False
            return True
        except Exception as exc:
            self._dirty = True
            QMessageBox.critical(self, _tr("Ręczny profil"), f"{_tr('Nie udało się zapisać zmian:\n')}{exc}")
            return False

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self._is_template():
            self._persist_template()
        elif self._ddd_profile is not None:
            self._persist_ddd()

    def _clear_ddd_profile(self) -> None:
        self._ddd_profile = None
        self._ddd_session = self._ddd_store.list_profile_summaries()
        self._cycle_start = None
        self._rebuild_ddd_combo()

    def _refresh(self) -> None:
        is_ddd = self._ddd_profile is not None and not self._is_template()
        self._refresh_open_activity()
        self._ddd_view.setEnabled(is_ddd)
        self._cycle_edit.setEnabled(not self._ddd_read_only())
        self.grid.editable = not self._ddd_read_only()
        self.grid.set_data_issues(self._ddd_store.get_data_issues(self._ddd_profile.profile_key) if is_ddd else [])
        self._rename_template_action.setEnabled(self._is_template())
        self._delete_template_action.setEnabled(self._is_template())
        # Extend visible weeks so open Max.6×24h deadline stays on the axis
        result = validate_scenario(self._scenario, zone=self._zone)
        if result.six_day_windows:
            last_deadline = max(w.end for w in result.six_day_windows)
            needed = ((last_deadline.date() - self._scenario.week_start).days // 7) + 1
            if needed > self._scenario.weeks:
                self._scenario.weeks = min(needed, 16)
                result = validate_scenario(self._scenario, zone=self._zone)

        self.grid.set_display_zone(self._zone)
        self.grid.set_scenario(self._scenario)
        self.grid.set_validation(result)
        self.findings.set_result(result)
        self.daily.set_days(totals_for_visible_scenario(self._scenario))
        title = self._scenario.title
        if self._path:
            title = f"{title} — {self._path.name}"
        self.setWindowTitle(f"Brudnopis OT — {title}")
        viol = sum(1 for f in result.findings if f.severity == "violation")
        warn = sum(1 for f in result.findings if f.severity == "warning")
        extra = ""
        if self._ddd_profile is not None:
            files = self._ddd_profile.filename
            extra = f" · {self._ddd_profile.profile_label}" if self._is_template() else f" · DDD: {self._ddd_profile.profile_label} · {files}"
        self._status.setText(
            f"{_tr('Tygodnie: ')}{self._scenario.weeks}{_tr(' · Bloki: ')}{len(self._scenario.blocks)}{_tr(' · Naruszenia: ')}{viol}{_tr(' · Ostrzeżenia: ')}{warn} · {zone_label(self._zone)}{extra}"
        )

    def _new_scenario(self) -> None:
        self._scenario = Scenario(
            week_start=ensure_monday(date.today()),
            weeks=5,
            title=_tr("Nowy scenariusz"),
        )
        self._path = None
        self._dirty = False
        self._clear_ddd_profile()
        self.grid.set_selected_ids(set())
        self._refresh()

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("Otwórz scenariusz"),
            str(_examples_dir() if _examples_dir().exists() else Path.home()),
            _tr("Scenariusz JSON (*.json)"),
        )
        if not path:
            return
        try:
            self._scenario = load_scenario(path)
            self._path = Path(path)
            self._dirty = False
            self._clear_ddd_profile()
            self._refresh()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _tr("Błąd"), f"{_tr('Nie udało się otworzyć:\n')}{exc}")

    def _save(self) -> None:
        if self._is_template():
            if self._persist_template():
                self.statusBar().showMessage(_tr("Zapisano ręczny profil w bazie Brudnopisu"), 3000)
            return
        if self._path is None:
            if self._ddd_profile is not None:
                if self._persist_ddd():
                    self.statusBar().showMessage(_tr('Korekty DDD zapisano w profilu kierowcy'), 3000)
                return
            self._save_as()
            return
        try:
            saved = save_scenario(self._scenario, self._path)
            self._path = saved
            self._dirty = False
            self.statusBar().showMessage(f"{_tr('Zapisano ')}{self._path}", 3000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _tr("Błąd"), f"{_tr('Nie udało się zapisać:\n')}{exc}")

    def _default_save_name(self) -> str:
        if self._ddd_profile is not None:
            base = safe_filename_part(self._ddd_profile.profile_label)
        else:
            base = safe_filename_part(self._scenario.title or "scenariusz")
        return f"{base}.json"

    def _save_as(self) -> None:
        suggested = scenarios_dir() / self._default_save_name()
        path, _ = QFileDialog.getSaveFileName(
            self,
            _tr("Zapisz scenariusz"),
            str(suggested),
            _tr("Scenariusz JSON (*.json)"),
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        if self._is_template():
            try:
                save_scenario(self._scenario, target)
                self.statusBar().showMessage(f"{_tr('Wyeksportowano ')}{target}", 3000)
            except Exception as exc:
                QMessageBox.critical(self, _tr("Błąd"), str(exc))
            return
        self._path = target
        self._save()

    def _report_profile_label(self) -> str:
        if self._ddd_profile is not None:
            return self._ddd_profile.profile_label
        return self._scenario.title or "scenariusz"

    def _export_pdf_report(self) -> None:
        profile_from = None
        profile_to = None
        if self._ddd_profile is not None:
            profile_from = self._ddd_profile.date_from
            profile_to = self._ddd_profile.date_to

        dlg = ReportPeriodDialog(
            self._scenario,
            profile_from=profile_from,
            profile_to=profile_to,
            parent=self,
        )
        try:
            if dlg.exec() != ReportPeriodDialog.DialogCode.Accepted:
                return
            start, end = dlg.selected_start(), dlg.selected_end()
        finally:
            dlg.deleteLater()
        try:
            from spedi.background import run_background
            snapshot = copy_scenario(self._scenario)
            label, zone = self._report_profile_label(), self._zone
            def render_report(task):
                from brudnopis_ot.core.report_pdf import write_profile_report
                return write_profile_report(snapshot, range_start=start, range_end=end,
                                            profile_label=label, zone=zone)
            path = run_background(self, _tr('Tworzenie raportu PDF…'), render_report)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, _tr("Raport PDF"), f"{_tr('Nie udało się wygenerować raportu:\n')}{exc}")
            return

        self.statusBar().showMessage(f"{_tr('Raport PDF: ')}{path}", 8000)
        reply = QMessageBox.information(
            self,
            _tr("Raport PDF"),
            f"Zapisano raport:\n{path}{_tr('\n\nOtworzyć plik?')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def _add_week(self) -> None:
        if self._ddd_read_only():
            return
        self._scenario.weeks += 1
        self._mark_dirty()
        self._refresh()

    def _remove_last_week(self) -> None:
        if self._ddd_read_only():
            return
        if self._scenario.weeks <= 1:
            QMessageBox.information(self, _tr("Tygodnie"), _tr("Musi pozostać co najmniej jeden tydzień."))
            return
        self._scenario.weeks -= 1
        self._mark_dirty()
        self._refresh()

    def _remove_first_week(self) -> None:
        if self._ddd_read_only():
            return
        if self._scenario.weeks <= 1:
            QMessageBox.information(self, _tr("Tygodnie"), _tr("Musi pozostać co najmniej jeden tydzień."))
            return
        self._scenario.week_start = self._scenario.week_start + timedelta(days=7)
        self._scenario.weeks -= 1
        self._mark_dirty()
        self._refresh()

    def _load_demo(self) -> None:
        if not self._confirm_overwrite(_tr("przykład")):
            return
        demo = _examples_dir() / "film_rekompensata_pm.json"
        if demo.exists():
            self._scenario = load_scenario(demo)
            self._path = demo
        else:
            self._scenario = _build_demo_scenario()
            self._path = None
        self._dirty = False
        self._clear_ddd_profile()
        self._refresh()

    def _on_zone_changed(self) -> None:
        self._zone = normalize_zone(self._zone_combo.currentData())
        QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_KEY, self._zone)
        self._refresh()

    def _new_block_at(self, instant: datetime) -> None:
        if self._ddd_read_only():
            return
        dlg = BlockEditorDialog(self, default_start=instant, zone=self._zone)
        if dlg.exec():
            block = dlg.result_block()
            if block is not None:
                self._scenario.add_block(block)
                self._mark_dirty()
                self._refresh()

    def _edit_block(self, block_id: str) -> None:
        if self._ddd_read_only():
            self.grid.set_selected_ids({block_id})
            self.statusBar().showMessage(_tr('Aby zmienić blok, wybierz widok Zmodyfikowany.'), 5000)
            return
        block = self._scenario.get_block(block_id)
        if block is None:
            return
        self.grid.set_selected_ids({block_id})
        dlg = BlockEditorDialog(self, block=block, zone=self._zone)
        if dlg.exec():
            if dlg.delete_requested:
                self._scenario.remove_block(block_id)
                if self._scenario.anchor_block_id == block_id:
                    self._scenario.anchor_block_id = None
            else:
                updated = dlg.result_block()
                if updated is not None:
                    self._scenario.remove_block(block_id)
                    self._scenario.add_block(updated)
                    if self._scenario.anchor_block_id == block_id:
                        self._scenario.anchor_block_id = updated.id
            self._mark_dirty()
            self._refresh()

    def _delete_block(self, block_id: str) -> None:
        if self._ddd_read_only():
            return
        if self._scenario.get_block(block_id) is None:
            return
        self._scenario.remove_block(block_id)
        if self._scenario.anchor_block_id == block_id:
            self._scenario.anchor_block_id = None
        self.grid.set_selected_ids(set())
        self._mark_dirty()
        self._refresh()

    def _set_week1_anchor(self, block_id: str) -> None:
        if self._ddd_read_only():
            return
        if self._ddd_profile is not None and not self._is_template():
            block = self._scenario.get_block(block_id)
            if block is None:
                return
            self._ddd_source.week_start = ensure_monday(block.start.date())
            self._ddd_source.anchor_block_id = block_id
            self._project_ddd()
        if self._is_template():
            block = self._scenario.get_block(block_id)
            if block is None:
                return
            # Obtain axis/anchor semantics from Scenario; retain the source history.
            self._scenario.set_week1_anchor(block_id)
            self._set_template_axis(self._scenario.week_start, block_id)
        try:
            block = self._scenario.set_week1_anchor(block_id)
        except KeyError:
            return
        self.grid.set_selected_ids({block_id})
        self._mark_dirty()
        self._refresh()
        self.statusBar().showMessage(
            f"{_tr('Tydzień 1 od ')}{self._scenario.week_start.strftime('%d.%m.%Y')} (kotwica: {block.start.strftime('%d.%m %H:%M')})",
            5000,
        )
    def _on_finding(self, finding) -> None:
        ids = set(finding.related_block_ids)
        self.grid.set_selected_ids(ids)

    def _on_day_selected(self, day: date) -> None:
        self.grid.set_highlight_day(day)


def _build_demo_scenario() -> Scenario:
    """Fallback demo if examples JSON missing (same as film PM tests)."""
    monday = date(2024, 11, 4)
    scenario = Scenario(
        week_start=monday,
        weeks=5,
        employer_country="PL",
        title=_tr("Film rekompensata PM"),
    )

    def rest(start: datetime, hours: float, bid: str, *, abroad: bool = False) -> Block:
        return Block(
            id=bid,
            kind=ActivityKind.REST,
            start=start,
            end=start + timedelta(hours=hours),
            started_abroad=abroad,
            country_start="SE" if abroad else "PL",
        )

    def drive(start: datetime, hours: float, bid: str) -> Block:
        return Block(
            id=bid,
            kind=ActivityKind.DRIVE,
            start=start,
            end=start + timedelta(hours=hours),
        )

    scenario.add_block(rest(datetime(2024, 11, 2, 12, 0), 45, "r_prev"))
    scenario.add_block(drive(datetime(2024, 11, 4, 9, 0), 48, "d1"))
    scenario.add_block(rest(datetime(2024, 11, 10, 8, 0), 24, "ot1", abroad=True))
    scenario.add_block(drive(datetime(2024, 11, 11, 8, 0), 48, "d2"))
    scenario.add_block(rest(datetime(2024, 11, 17, 6, 0), 24, "ot2", abroad=True))
    scenario.add_block(drive(datetime(2024, 11, 18, 6, 0), 40, "d3"))
    scenario.add_block(rest(datetime(2024, 11, 19, 22, 0), 87, "ot3"))
    scenario.add_block(drive(datetime(2024, 11, 23, 13, 0), 30, "d4"))
    scenario.add_block(rest(datetime(2024, 11, 24, 19, 0), 45, "ot4"))
    return scenario
