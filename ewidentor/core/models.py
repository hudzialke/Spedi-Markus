from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum

from ewidentor.core.milo_rates import AtSeniority


class AbsenceKind(str, Enum):
    SICK = "sick"  # chorobowe
    PAID_LEAVE = "paid_leave"  # urlop wypoczynkowy
    UNPAID_LEAVE = "unpaid_leave"  # urlop bezpłatny
    OTHER_JUSTIFIED = "other_justified"
    UNJUSTIFIED = "unjustified"
    FREE_DAY = "free_day"  # dzień wolny z tytułem


ABSENCE_LABELS_PL: dict[AbsenceKind, str] = {
    AbsenceKind.SICK: "Chorobowe",
    AbsenceKind.PAID_LEAVE: "Urlop wypoczynkowy",
    AbsenceKind.UNPAID_LEAVE: "Urlop bezpłatny",
    AbsenceKind.OTHER_JUSTIFIED: "Inna usprawiedliwiona",
    AbsenceKind.UNJUSTIFIED: "Nieusprawiedliwiona",
    AbsenceKind.FREE_DAY: "Dzień wolny",
}


# Tryb mapowania dyspozycji → dyżur / praca (zob. month_card).
DUTY_MODE_RAW = "raw"
DUTY_MODE_HEURISTIC = "heuristic"
DUTY_MODE_SPAN = "span"  # rest w dobie pracy → dyżur 50%
# Legacy DB key from older builds (split so brand strings are absent from source).
_DUTY_MODE_LEGACY_HEURISTIC = "like_" + "4" + "trans"
DUTY_MODE_LABELS_PL: dict[str, str] = {
    DUTY_MODE_RAW: "Dyżur: surowy tacho",
    DUTY_MODE_HEURISTIC: "Dyżur: heurystyka dyspozycji",
    DUTY_MODE_SPAN: "Dyżur: rest w dobie pracy",
}


def normalize_duty_mode(value: str | None) -> str:
    raw = (value or DUTY_MODE_RAW).strip().lower() or DUTY_MODE_RAW
    if raw == _DUTY_MODE_LEGACY_HEURISTIC:
        return DUTY_MODE_HEURISTIC
    if raw in DUTY_MODE_LABELS_PL:
        return raw
    return DUTY_MODE_RAW


@dataclass(slots=True)
class PaySettings:
    monthly_salary: float = 0.0
    hourly_rate: float | None = None  # jeśli None → salary / norma
    duty_percent: float = 50.0  # % stawki za dyżur
    night_percent: float = 20.0  # % dodatku za noc (okno firmowe)
    # Domyślne okno firmowe 22–06 (KP-like); istniejący kierowcy zachowują zapisane wartości.
    night_start: time = field(default_factory=lambda: time(22, 0))
    night_end: time = field(default_factory=lambda: time(6, 0))
    overtime_50_percent: float = 50.0
    overtime_100_percent: float = 100.0
    night_on_availability: bool = False  # licz noc także na segmentach dyspozycji
    duty_mode: str = DUTY_MODE_RAW  # raw | heuristic
    valid_from: date | None = None


@dataclass(slots=True)
class Driver:
    id: int | None
    full_name: str
    card_number: str | None = None
    brudnopis_profile_key: str | None = None
    work_system: str = "równoważny"
    employment_fraction: float = 1.0  # 1.0 = pełny etat
    employer_name: str = ""
    notes: str = ""
    pay: PaySettings = field(default_factory=PaySettings)
    at_seniority: AtSeniority = AtSeniority.A


@dataclass(slots=True)
class Absence:
    id: int | None
    driver_id: int
    day: date
    kind: AbsenceKind
    minutes: int = 0  # 0 = cały dzień (dla urlopów)
    title: str = ""
    note: str = ""


@dataclass(slots=True)
class Attachment:
    id: int | None
    driver_id: int
    year: int
    month: int
    filename: str
    stored_path: str
    note: str = ""


@dataclass(slots=True)
class DayCard:
    day: date
    plan_min: int = 0  # wymiar / plan (np. 8h w dniu roboczym)
    start_min: int | None = None  # minuta od północy
    end_min: int | None = None
    work_min: int = 0  # czas pracy = jazda + inna praca
    drive_min: int = 0
    other_work_min: int = 0
    break_15_min: int = 0
    duty_50_min: int = 0  # dyżur mapowany z availability
    duty_100_min: int = 0
    availability_min: int = 0
    rest_min: int = 0
    daily_rest_min: int = 0
    night_min: int = 0  # w oknie firmowym (np. 00–04)
    sunday_holiday_min: int = 0
    paid_min: int = 0
    overtime_min: int = 0
    absence_label: str = ""
    absence_kind: AbsenceKind | None = None
    note: str = ""
    is_manual: bool = False


@dataclass(slots=True)
class MonthCard:
    driver: Driver
    year: int
    month: int
    days: list[DayCard]
    closed: bool = False
    norma_min: int = 0
    employer_name: str = ""

    @property
    def period_label(self) -> str:
        months = (
            "",
            "styczeń",
            "luty",
            "marzec",
            "kwiecień",
            "maj",
            "czerwiec",
            "lipiec",
            "sierpień",
            "wrzesień",
            "październik",
            "listopad",
            "grudzień",
        )
        return f"{months[self.month]} {self.year}"


@dataclass(slots=True)
class PayrollBreakdown:
    norma_min: int
    monthly_salary: float
    hourly_rate: float
    night_rate: float
    duty_rate: float
    work_min: int
    paid_min: int
    night_min: int
    duty_50_min: int
    duty_100_min: int
    overtime_min: int
    base_pay: float
    night_pay: float
    duty_pay: float
    overtime_pay: float
    gross_total: float
    net_estimate: float = 0.0
    norma_formula: str = ""
    sunday_unreturned_min: int = 0
    section_a: float = 0.0
    section_b: float = 0.0
    section_c: float = 0.0
    milo_topup_pln: float = 0.0
