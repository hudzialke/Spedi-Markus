from __future__ import annotations

from spedi.i18n import tr as _tr

from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ddd_analyzer.display_tz import (
    DEFAULT_ZONE,
    SETTINGS_APP,
    SETTINGS_KEY,
    SETTINGS_ORG,
    DisplayZone,
    format_dt,
    normalize_zone,
    zone_label,
)
from ddd_analyzer.models import DddAnalysis, DddFileKind, PairedAnalysis
from ddd_analyzer.night_work import analyze_night_work
from ddd_analyzer.pairing import data_range_text, pair_analyses
from ddd_analyzer.parser import parse_ddd_file
from ddd_analyzer.reg561 import analyze_rests
from ddd_analyzer.summarize import build_week_summary
from ddd_analyzer.ui.night_work_panel import NightWorkPanel
from ddd_analyzer.ui.rest_panel import RestPanel
from ddd_analyzer.ui.summary_panel import SummaryPanel
from ddd_analyzer.ui.timeline_widget import TimelineWidget


def _vline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setFixedWidth(1)
    return line


def _id_chip(title: str, value: str = "—") -> QLabel:
    chip = QLabel(f"<b>{title}:</b> {value}")
    chip.setTextFormat(Qt.TextFormat.RichText)
    chip.setWordWrap(False)
    chip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    chip.setStyleSheet("padding: 2px 4px; font-size: 12px;")
    return chip


def _set_chip(chip: QLabel, title: str, value: str, *, color: str | None = None) -> None:
    style = f"color: {color};" if color else ""
    chip.setText(f"<span style='{style}'><b>{title}:</b> {value}</span>")


class CompactDropZone(QLabel):
    """Male pole drop/klik w rogu ekranu."""

    def __init__(
        self,
        short_label: str,
        file_kind: DddFileKind,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(short_label, parent)
        self._file_kind = file_kind
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(132, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"{_tr('Przeciagnij ')}{short_label} lub kliknij, aby otworzyc plik .ddd"
        )
        self.setStyleSheet(
            "QLabel { border: 1px dashed #d0d7de; padding: 4px; "
            "background: #ffffff; color: #0b3d4a; font-size: 10px; font-weight: 600; }"
            "QLabel:hover { background: #eef6f8; border-color: #0b3d4a; }"
        )
        self.on_file_dropped: Callable[[str], None] = lambda _path: None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".ddd"):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".ddd"):
                self.on_file_dropped(path)
                event.acceptProposedAction()
                return

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.on_file_dropped("__browse__")
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("DDD Analyzer — analiza plikow tachografu"))
        self.resize(1180, 820)
        self.setMinimumSize(900, 560)
        if parent is not None:
            self.setWindowFlags(Qt.WindowType.Widget)

        self._card: DddAnalysis | None = None
        self._vu: DddAnalysis | None = None
        self._paired: PairedAnalysis | None = None
        self._active: DddAnalysis | None = None
        self._zone: DisplayZone = normalize_zone(
            QSettings(SETTINGS_ORG, SETTINGS_APP).value(SETTINGS_KEY, DEFAULT_ZONE)
        )

        outer = QWidget()
        self.setCentralWidget(outer)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.setSpacing(6)

        top_row = QHBoxLayout()
        privacy = QLabel(_tr("Analiza .ddd lokalnie na tym komputerze."))
        privacy.setStyleSheet("color: #5a6672; font-size: 11px;")
        top_row.addWidget(privacy, stretch=1)

        self.card_drop = CompactDropZone(_tr("Karta C_"), DddFileKind.CARD)
        self.vu_drop = CompactDropZone(_tr("Pamiec VU"), DddFileKind.VU)
        self.card_drop.on_file_dropped = lambda p: self._on_drop(p, DddFileKind.CARD)
        self.vu_drop.on_file_dropped = lambda p: self._on_drop(p, DddFileKind.VU)
        top_row.addWidget(self.card_drop)
        top_row.addWidget(self.vu_drop)
        outer_layout.addLayout(top_row)

        tools_row = QHBoxLayout()
        tools_row.addWidget(QLabel(_tr("Strefa:")))
        self.zone_combo = QComboBox()
        self.zone_combo.addItem(zone_label("Europe/Warsaw"), "Europe/Warsaw")
        self.zone_combo.addItem(zone_label("UTC"), "UTC")
        idx = self.zone_combo.findData(self._zone)
        if idx >= 0:
            self.zone_combo.setCurrentIndex(idx)
        self.zone_combo.currentIndexChanged.connect(self._on_zone_changed)
        tools_row.addWidget(self.zone_combo)

        self.btn_rest = QPushButton(_tr("Odpoczynki 561"))
        self.btn_rest.clicked.connect(self._show_rest_dialog)
        self.btn_rest.setEnabled(False)
        tools_row.addWidget(self.btn_rest)

        self.btn_night = QPushButton(_tr("Pora nocna"))
        self.btn_night.clicked.connect(self._show_night_dialog)
        self.btn_night.setEnabled(False)
        tools_row.addWidget(self.btn_night)

        tools_row.addStretch()
        outer_layout.addLayout(tools_row)

        self.header_box = QGroupBox(_tr("Identyfikacja"))
        header_box_layout = QVBoxLayout(self.header_box)
        header_box_layout.setContentsMargins(8, 6, 8, 6)
        header_box_layout.setSpacing(4)

        id_row = QHBoxLayout()
        id_row.setSpacing(8)
        self.chip_driver = _id_chip(_tr("Kierowca"))
        self.chip_card = _id_chip(_tr("Nr karty"))
        self.chip_generation = _id_chip(_tr("Generacja"))
        self.chip_range = _id_chip(_tr("Zakres"))
        self.chip_vehicle = _id_chip(_tr("Pojazd"))
        self.chip_match = _id_chip("Karta/VU")
        for chip in (
            self.chip_driver,
            self.chip_card,
            self.chip_generation,
            self.chip_range,
            self.chip_vehicle,
            self.chip_match,
        ):
            id_row.addWidget(chip)
            id_row.addWidget(_vline())
        id_row.takeAt(id_row.count() - 1)
        id_row.addStretch()

        id_host = QWidget()
        id_host.setLayout(id_row)
        id_scroll = QScrollArea()
        id_scroll.setWidget(id_host)
        id_scroll.setWidgetResizable(True)
        id_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        id_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        id_scroll.setMaximumHeight(40)
        id_scroll.setFrameShape(QFrame.Shape.NoFrame)
        header_box_layout.addWidget(id_scroll)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(_tr("Zrodlo aktywnosci:")))
        self.source_combo = QComboBox()
        self.source_combo.addItem(_tr("Karta kierowcy"), "card")
        self.source_combo.addItem(_tr("Pamiec VU"), "vu")
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_combo)
        source_row.addStretch()
        header_box_layout.addLayout(source_row)

        vehicles_label = QLabel(_tr("Pojazdy"))
        vehicles_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #455a64;")
        header_box_layout.addWidget(vehicles_label)

        self.vehicles_table = QTableWidget(0, 6)
        self.vehicles_table.setHorizontalHeaderLabels(
            [_tr("Tablica"), _tr("Kraj"), "VIN", _tr("Odometr od"), _tr("Odometr do"), _tr("Okres uzycia")]
        )
        self.vehicles_table.horizontalHeader().setStretchLastSection(True)
        self.vehicles_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.vehicles_table.setAlternatingRowColors(True)
        self.vehicles_table.verticalHeader().setVisible(False)
        self.vehicles_table.setMaximumHeight(88)
        self.vehicles_table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        header_box_layout.addWidget(self.vehicles_table)
        outer_layout.addWidget(self.header_box)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)
        self.main_splitter.setStyleSheet(
            "QSplitter::handle { background: #d0d7de; margin: 0 2px; min-width: 6px; }"
            "QSplitter::handle:hover { background: #0b3d4a; }"
        )

        timeline_box = QGroupBox(_tr("Os czasu (dzien)"))
        timeline_layout = QVBoxLayout(timeline_box)
        timeline_layout.setContentsMargins(6, 8, 6, 6)
        self.timeline = TimelineWidget()
        timeline_layout.addWidget(self.timeline)
        self.main_splitter.addWidget(timeline_box)

        summary_box = QGroupBox(_tr("Podsumowanie dni"))
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setContentsMargins(6, 8, 6, 6)
        self.summary = SummaryPanel()
        summary_layout.addWidget(self.summary)
        self.main_splitter.addWidget(summary_box)

        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([620, 420])
        outer_layout.addWidget(self.main_splitter, stretch=1)

        self.summary.table.itemSelectionChanged.connect(self._on_day_selected)
        self.setStatusBar(QStatusBar())

    def _on_drop(self, path: str, kind: DddFileKind) -> None:
        if path == "__browse__":
            self._browse(kind)
        else:
            self._load_file(path, kind)

    def _browse(self, kind: DddFileKind) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            _tr("Wybierz plik .ddd"),
            str(Path.home() / "Downloads"),
            _tr("Pliki tachografu (*.ddd)"),
        )
        if path:
            self._load_file(path, kind)

    def _load_file(self, path: str, expected_kind: DddFileKind | None = None) -> None:
        try:
            analysis = parse_ddd_file(path)
        except Exception as exc:
            QMessageBox.critical(self, _tr("Blad odczytu"), str(exc))
            return

        if expected_kind == DddFileKind.CARD and analysis.file_kind == DddFileKind.VU:
            QMessageBox.warning(
                self,
                _tr("Nie ten typ pliku"),
                _tr("Wyglada na plik pamieci VU (M_). Uzyj prawego pola."),
            )
            self._vu = analysis
        elif expected_kind == DddFileKind.VU and analysis.file_kind == DddFileKind.CARD:
            QMessageBox.warning(
                self,
                _tr("Nie ten typ pliku"),
                _tr("Wyglada na karte kierowcy (C_). Uzyj lewego pola."),
            )
            self._card = analysis
        elif analysis.file_kind == DddFileKind.VU:
            self._vu = analysis
        else:
            self._card = analysis

        if self._card:
            name = Path(self._card.source_path).name
            if len(name) > 14:
                name = name[:11] + "..."
            self.card_drop.setText(f"{_tr('Karta: ')}{name}")
        if self._vu:
            name = Path(self._vu.source_path).name
            if len(name) > 14:
                name = name[:11] + "..."
            self.vu_drop.setText(f"VU: {name}")

        self._paired = pair_analyses(self._card, self._vu)
        self._refresh_view()

    def load_profile(self, profile) -> None:
        """Display the merged, attributed raw profile (not a vehicle's last file)."""
        from ddd_analyzer.models import ActivitySegment, ActivityType, DriverInfo, VehicleRef, BorderCrossing, PlaceRecord, GnssPoint
        from ddd_analyzer.summarize import build_daily_summaries
        segments = [ActivitySegment(s.day, ActivityType(s.activity), s.start_minute, s.end_minute)
                    for s in profile.segments]
        kind = DddFileKind.VU if profile.source_kind == "vu" else DddFileKind.CARD
        analysis = DddAnalysis(source_path="", file_kind=kind, generation=_tr("Profil DDD"),
            is_vu=kind == DddFileKind.VU,
            driver=DriverInfo(card_number=profile.card_number or "", surname=profile.driver_name),
            vehicle=VehicleRef(plate=profile.vehicle_plate or ""), vehicles=[],
            segments=segments, daily_summaries=build_daily_summaries(segments, {}),
            date_range=(profile.date_from, profile.date_to), filename=profile.filename,
            border_crossings=[BorderCrossing(c.occurred_at, c.country_left, c.country_entered, c.odometer_km, source=c.source) for c in profile.border_crossings],
            places=[PlaceRecord(p.occurred_at, p.entry_type, p.country_code, p.odometer_km) for p in profile.places],
            gnss_points=[GnssPoint(p.occurred_at, p.latitude, p.longitude, p.odometer_km) for p in profile.gnss_points])
        analysis.open_activity = profile.open_activity
        if analysis.open_activity and not any(s.day == analysis.open_activity.start.date() for s in analysis.daily_summaries):
            from ddd_analyzer.models import DailySummary
            analysis.daily_summaries.append(DailySummary(analysis.open_activity.start.date()))
            analysis.daily_summaries.sort(key=lambda s: s.day)
        self._card = analysis if kind == DddFileKind.CARD else None
        self._vu = analysis if kind == DddFileKind.VU else None
        self.card_drop.setText(_tr("Karta: profil scalony") if self._card else _tr("Karta"))
        self.vu_drop.setText(_tr("VU: przypisany kierowca") if self._vu else "VU")
        self._paired = pair_analyses(self._card, self._vu)
        self._refresh_view()

    def load_paths(self, paths: list[str] | tuple[str, ...]) -> None:
        """Wczytaj kartę i/lub VU z archiwum profilu Brudnopisu."""
        self._card = None
        self._vu = None
        self._paired = None
        self._active = None
        for path in paths:
            if path and Path(path).is_file():
                self._load_file(path, expected_kind=None)

    def _show_rest_dialog(self) -> None:
        active = self._active
        if not active or not active.segments:
            QMessageBox.information(
                self,
                _tr("Odpoczynki 561"),
                _tr("Brak aktywnosci — wczytaj plik z danymi kierowcy."),
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(_tr("Odpoczynki 561/2006"))
        dialog.resize(960, 520)
        layout = QVBoxLayout(dialog)
        panel = RestPanel()
        panel.set_summary(analyze_rests(active.segments), zone=self._zone)
        layout.addWidget(panel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _show_night_dialog(self) -> None:
        active = self._active
        if not active or not active.segments:
            QMessageBox.information(
                self,
                _tr("Pora nocna"),
                _tr("Brak aktywnosci — wczytaj plik z danymi kierowcy."),
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(_tr("Pora nocna (art. 21)"))
        dialog.resize(820, 420)
        layout = QVBoxLayout(dialog)
        panel = NightWorkPanel()
        panel.set_analysis(analyze_night_work(active.segments, zone=self._zone))
        layout.addWidget(panel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _refresh_view(self) -> None:
        self._active = self._choose_active_analysis()
        self._update_header()
        self._update_vehicles()
        self._update_activity_view()

    def _choose_active_analysis(self) -> DddAnalysis | None:
        prefer = self.source_combo.currentData()
        if prefer == "vu" and self._vu:
            return self._vu
        if self._card:
            return self._card
        return self._vu

    def _update_header(self) -> None:
        card = self._card
        vu = self._vu
        active = self._active

        if card:
            _set_chip(self.chip_driver, _tr("Kierowca"), card.driver.display_name)
            _set_chip(self.chip_card, _tr("Nr karty"), card.driver.card_number or "—")
        elif vu:
            _set_chip(self.chip_driver, _tr("Kierowca"), vu.driver.display_name if vu.driver.card_number else _tr("Brak przypisanej karty (VU)"))
            _set_chip(self.chip_card, _tr("Nr karty"), vu.driver.card_number or "—")
        else:
            _set_chip(self.chip_driver, _tr("Kierowca"), "—")
            _set_chip(self.chip_card, _tr("Nr karty"), "—")

        if active:
            _set_chip(self.chip_generation, _tr("Generacja"), active.generation)
            _set_chip(self.chip_range, _tr("Zakres"), data_range_text(active))
            vehicle = active.vehicle
            vin = vehicle.vin if vehicle.vin not in {"", "N/A"} else "brak VIN"
            _set_chip(self.chip_vehicle, _tr("Pojazd"), f"{vehicle.display_plate} · VIN {vin}")
        else:
            _set_chip(self.chip_generation, _tr("Generacja"), "—")
            _set_chip(self.chip_range, _tr("Zakres"), "—")
            _set_chip(self.chip_vehicle, _tr("Pojazd"), "—")

        match = self._paired.vehicle_match if self._paired else None
        if match is None:
            _set_chip(self.chip_match, "Karta/VU", "wczytaj oba pliki", color="#5a6672")
        elif match.is_consistent:
            text = f"OK · {match.card_plate}"
            if match.vin_match is True:
                text += f" · VIN {match.vu_vin}"
            elif match.vin_match is False:
                text += " · VIN rozny"
            _set_chip(self.chip_match, "Karta/VU", text, color="#0b3d4a")
        else:
            _set_chip(
                self.chip_match,
                "Karta/VU",
                f"roznie · karta {match.card_plate}, VU {match.vu_plate}",
                color="#e11d48",
            )

        if vu and not card:
            _set_chip(self.chip_range, _tr("Zakres"), f"VU: {data_range_text(vu)}")

    def _update_vehicles(self) -> None:
        vehicles = []
        if self._card:
            vehicles.extend(self._card.vehicles)
        if self._vu and self._vu.vehicle.plate:
            vu_vehicle = self._vu.vehicle
            if not any(v.plate == vu_vehicle.plate for v in vehicles):
                vehicles.append(vu_vehicle)

        self.vehicles_table.setRowCount(len(vehicles))
        for row, vehicle in enumerate(vehicles):
            period = "—"
            if vehicle.first_use and vehicle.last_use:
                period = (
                    f"{format_dt(vehicle.first_use, self._zone)} – "
                    f"{format_dt(vehicle.last_use, self._zone)}"
                )
            values = [
                vehicle.display_plate,
                vehicle.registration_nation or "—",
                vehicle.vin if vehicle.vin not in {"", "N/A"} else "—",
                str(vehicle.odometer_begin) if vehicle.odometer_begin is not None else "—",
                str(vehicle.odometer_end) if vehicle.odometer_end is not None else "—",
                period,
            ]
            for col, value in enumerate(values):
                self.vehicles_table.setItem(row, col, QTableWidgetItem(value))
        self.vehicles_table.resizeColumnsToContents()

    def _update_activity_view(self) -> None:
        active = self._active
        if not active or not active.daily_summaries:
            self.summary.set_summaries([])
            self.timeline.set_day(date.today(), [])
            self.btn_rest.setEnabled(False)
            self.btn_night.setEnabled(False)
            self.statusBar().showMessage(_tr("Brak aktywnosci w wybranym pliku"))
            return

        anchor = active.daily_summaries[-1].day
        week = build_week_summary(active.daily_summaries, anchor)
        self.summary.set_summaries(active.daily_summaries, week)
        if self.summary.table.rowCount() > 0:
            self.summary.table.selectRow(self.summary.table.rowCount() - 1)
        self.timeline.set_day(anchor, active.segments, zone=self._zone, open_activity=active.open_activity)

        night_analysis = analyze_night_work(active.segments, zone=self._zone)

        source_name = "karty" if active.file_kind == DddFileKind.CARD else "VU"
        status = (
            f"{_tr('Wczytano ')}{len(active.segments)} segmentow z {source_name}: {active.filename}  ·  {zone_label(self._zone)}"
        )
        if night_analysis.has_violations:
            status += (
                f"  |  OSTRZEZENIE: {len(night_analysis.violations)} naruszen "
                f"limitu 10 h w porze nocnej"
            )
        if active.open_activity:
            from ddd_analyzer.models import ACTIVITY_LABELS_PL
            status += f" | {ACTIVITY_LABELS_PL[active.open_activity.activity]} {format_dt(active.open_activity.start, self._zone)} · {_tr('Nieznany koniec')}"
        self.btn_rest.setEnabled(True)
        self.btn_night.setEnabled(True)
        self.statusBar().showMessage(status)

    def _on_zone_changed(self) -> None:
        zone = normalize_zone(self.zone_combo.currentData())
        self._zone = zone
        QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(SETTINGS_KEY, zone)
        self._update_vehicles()
        self._update_activity_view()

    def _on_source_changed(self) -> None:
        self._active = self._choose_active_analysis()
        self._update_header()
        self._update_activity_view()

    def _on_day_selected(self) -> None:
        if not self._active:
            return
        day = self.summary.selected_day()
        if day:
            self.timeline.set_day(day, self._active.segments, zone=self._zone, open_activity=self._active.open_activity)
