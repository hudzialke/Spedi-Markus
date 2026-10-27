from __future__ import annotations

from spedi.i18n import tr as _tr

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class ActivityType(str, Enum):
    REST = "rest"
    AVAILABILITY = "availability"
    WORK = "work"
    DRIVE = "drive"


class DddFileKind(str, Enum):
    CARD = "card"
    VU = "vu"
    UNKNOWN = "unknown"


ACTIVITY_LABELS_PL: dict[ActivityType, str] = {
    ActivityType.REST: _tr("Odpoczynek"),
    ActivityType.AVAILABILITY: _tr("Dyspozycyjnosc"),
    ActivityType.WORK: _tr("Praca"),
    ActivityType.DRIVE: _tr("Jazda"),
}

ACTIVITY_COLORS: dict[ActivityType, str] = {
    ActivityType.REST: "#9e9e9e",
    ActivityType.AVAILABILITY: "#4caf50",
    ActivityType.WORK: "#ffc107",
    ActivityType.DRIVE: "#2196f3",
}


@dataclass(slots=True)
class DriverInfo:
    card_number: str = ""
    surname: str = ""
    firstname: str = ""
    birth_date: str = ""
    expiry_date: str = ""
    issuing_nation: str = ""
    licence_number: str = ""

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.firstname, self.surname) if p and p != "N/A"]
        return " ".join(parts) or "Nieznany kierowca"


@dataclass(slots=True)
class VehicleRef:
    plate: str = ""
    registration_nation: str = ""
    vin: str = ""
    odometer_begin: int | None = None
    odometer_end: int | None = None
    first_use: datetime | None = None
    last_use: datetime | None = None
    source: str = "card"

    @property
    def display_plate(self) -> str:
        nation = self.registration_nation.strip()
        plate = self.plate.strip()
        if nation and nation != "N/A":
            return f"{nation} {plate}".strip()
        return plate


@dataclass(slots=True)
class ActivitySegment:
    day: date
    activity: ActivityType
    start_minute: int
    end_minute: int

    @property
    def duration_min(self) -> int:
        return max(0, self.end_minute - self.start_minute)


@dataclass(frozen=True, slots=True)
class OpenActivity:
    """A recorded change whose end is unknown; start is canonical naive UTC."""

    activity: ActivityType
    start: datetime


@dataclass(slots=True)
class BorderCrossing:
    """Przekroczenie granicy (karta 0x052A / VU 0x22 / wyliczone z GNSS)."""

    timestamp: datetime
    country_left: str
    country_entered: str
    odometer_km: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    source: str = "card"  # card | gnss


@dataclass(slots=True)
class GnssPoint:
    """Punkt GNSS Accumulated Driving (0x0525) — lat/lon + czas."""

    occurred_at: datetime
    latitude: float
    longitude: float
    odometer_km: int | None = None


@dataclass(slots=True)
class PlaceRecord:
    """Miejsce START/END okresu pracy dziennej (karta 0x0506 / VU places)."""

    timestamp: datetime
    entry_type: str  # START | END
    country_code: str  # ISO-2
    odometer_km: int | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(slots=True)
class DailySummary:
    day: date
    drive_min: int = 0
    work_min: int = 0
    availability_min: int = 0
    rest_min: int = 0
    odometer_km: int | None = None


@dataclass(slots=True)
class WeekSummary:
    week_start: date
    days: list[DailySummary] = field(default_factory=list)

    @property
    def drive_min(self) -> int:
        return sum(d.drive_min for d in self.days)

    @property
    def work_min(self) -> int:
        return sum(d.work_min for d in self.days)

    @property
    def availability_min(self) -> int:
        return sum(d.availability_min for d in self.days)

    @property
    def rest_min(self) -> int:
        return sum(d.rest_min for d in self.days)


@dataclass(slots=True)
class VehicleMatch:
    plate_match: bool
    vin_match: bool | None
    card_plate: str
    vu_plate: str
    card_vin: str
    vu_vin: str

    @property
    def is_consistent(self) -> bool:
        if not self.plate_match:
            return False
        if self.vin_match is None:
            return True
        return self.vin_match


@dataclass(slots=True)
class DddAnalysis:
    source_path: str
    file_kind: DddFileKind
    generation: str
    is_vu: bool
    driver: DriverInfo
    vehicle: VehicleRef
    vehicles: list[VehicleRef]
    segments: list[ActivitySegment]
    daily_summaries: list[DailySummary]
    date_range: tuple[date | None, date | None]
    driver_analyses: list[DddAnalysis] = field(default_factory=list)
    filename: str = ""
    read_at: datetime | None = None
    border_crossings: list[BorderCrossing] = field(default_factory=list)
    places: list[PlaceRecord] = field(default_factory=list)
    gnss_points: list[GnssPoint] = field(default_factory=list)
    open_activity: OpenActivity | None = None

    @property
    def activity_days(self) -> list[date]:
        return sorted({s.day for s in self.segments} | ({self.open_activity.start.date()} if self.open_activity else set()))


@dataclass(slots=True)
class PairedAnalysis:
    card: DddAnalysis | None
    vu: DddAnalysis | None
    vehicle_match: VehicleMatch | None = None


class RestKind(str, Enum):
    OTHER = "other_rest"
    DAILY_REDUCED = "daily_reduced"
    DAILY_REGULAR = "daily_regular"
    WEEKLY_REDUCED = "weekly_reduced"
    WEEKLY_REGULAR = "weekly_regular"


REST_KIND_LABELS_PL: dict[RestKind, str] = {
    RestKind.OTHER: "Krotki odpoczynek (<9h)",
    RestKind.DAILY_REDUCED: "Dobowy skrocony (9–11h)",
    RestKind.DAILY_REGULAR: "Dobowy regularny (11–24h)",
    RestKind.WEEKLY_REDUCED: "Tygodniowy skrocony (24–45h)",
    RestKind.WEEKLY_REGULAR: "Tygodniowy regularny (>=45h)",
}


@dataclass(slots=True)
class ContinuousRestBlock:
    start: datetime
    end: datetime

    @property
    def duration_min(self) -> int:
        return max(0, int((self.end - self.start).total_seconds() // 60))


@dataclass(slots=True)
class ClassifiedRest:
    start: datetime
    end: datetime
    duration_min: int
    kind: RestKind
    compensation_min: int = 0
    debt_created_min: int = 0
    debt_after_min: int = 0
    deadline: date | None = None
    status: str = "OK"
    note: str = ""

    @property
    def kind_label_pl(self) -> str:
        return REST_KIND_LABELS_PL[self.kind]


@dataclass(slots=True)
class RestAnalysisSummary:
    classified: list[ClassifiedRest]
    unpaid_compensation_min: int
    weekly_reduced_count: int
    weekly_regular_count: int
    daily_reduced_count: int
    daily_regular_count: int


@dataclass(slots=True)
class NightWorkWarning:
    """Naruszenie art. 21 ustawy o czasie pracy kierowcow (limit 10 h w porze nocnej)."""

    period_start: datetime
    period_end: datetime
    work_min: int
    night_work_min: int
    over_min: int
    night_window_label: str

    @property
    def message_pl(self) -> str:
        from ddd_analyzer.summarize import format_minutes

        start = self.period_start.strftime("%d.%m.%Y %H:%M")
        end = self.period_end.strftime("%d.%m.%Y %H:%M")
        return (
            f"Pora nocna ({self.night_window_label}): czas pracy {format_minutes(self.work_min)} "
            f"przekracza limit 10 h miedzy odpoczynkami ({start} – {end}). "
            f"Nadwyzka: {format_minutes(self.over_min)}."
        )
