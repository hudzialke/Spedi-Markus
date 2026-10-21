from __future__ import annotations

from spedi.i18n import tr as _tr

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Literal
from uuid import uuid4


class ActivityKind(str, Enum):
    DRIVE = "drive"
    WORK = "work"
    AVAILABILITY = "availability"
    REST = "rest"
    CARD_OUT = "card_out"
    CREW = "crew"


ACTIVITY_LABELS_PL: dict[ActivityKind, str] = {
    ActivityKind.DRIVE: _tr("Jazda"),
    ActivityKind.WORK: _tr("Praca"),
    ActivityKind.AVAILABILITY: _tr("Dyspozycyjność"),
    ActivityKind.REST: _tr("Postój / odpoczynek"),
    ActivityKind.CARD_OUT: _tr("Karta wyjęta"),
    ActivityKind.CREW: _tr("Załoga"),
}

ACTIVITY_COLORS: dict[ActivityKind, str] = {
    ActivityKind.DRIVE: "#e6b800",
    ActivityKind.WORK: "#0b3d4a",
    ActivityKind.AVAILABILITY: "#1cfc03",
    ActivityKind.REST: "#7eb8c4",
    ActivityKind.CARD_OUT: "#f7f9fb",
    ActivityKind.CREW: "#5a6672",
}

# Pasek rekompensaty na odpoczynku (nadmiar ponad 45 h)
COMPENSATION_BAR_COLOR = "#128c7e"

Severity = Literal["ok", "info", "warning", "violation"]
WeeklyRestClass = Literal["none", "reduced", "regular", "too_short"]


def _new_id() -> str:
    return uuid4().hex[:12]


@dataclass(slots=True)
class Block:
    kind: ActivityKind
    start: datetime
    end: datetime
    id: str = field(default_factory=_new_id)
    country_start: str | None = None
    started_abroad: bool = False
    label: str | None = None

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("Block end must be after start")

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def duration_hours(self) -> float:
        return self.duration.total_seconds() / 3600.0


@dataclass(slots=True)
class Scenario:
    week_start: date
    weeks: int = 5
    employer_country: str = "PL"
    title: str = "Nowy scenariusz"
    blocks: list[Block] = field(default_factory=list)
    # Blok oznaczony jako kotwica „start tygodnia 1” (opcjonalnie)
    anchor_block_id: str | None = None

    def __post_init__(self) -> None:
        if self.week_start.weekday() != 0:
            raise ValueError("week_start must be a Monday")
        if self.weeks < 1:
            raise ValueError("weeks must be >= 1")

    def set_axis_monday(self, monday: date) -> None:
        """Ustaw poniedziałek osi i odetnij bloki sprzed tej daty (z wyjątkiem OT ≥24h)."""
        from brudnopis_ot.core.calendar_weeks import ensure_monday
        from dataclasses import replace

        self.week_start = ensure_monday(monday)
        axis_start_dt = datetime.combine(self.week_start, datetime.min.time())
        kept: list[Block] = []
        for b in self.blocks:
            if b.end <= axis_start_dt:
                if (
                    b.kind == ActivityKind.REST
                    and b.duration_hours >= 24.0 - 1e-6
                    and b.end + timedelta(days=6) > axis_start_dt
                ):
                    kept.append(b)
                continue
            if b.start < axis_start_dt < b.end:
                if (
                    b.kind == ActivityKind.REST
                    and b.duration_hours >= 24.0 - 1e-6
                ):
                    kept.append(b)
                else:
                    kept.append(replace(b, start=axis_start_dt))
            else:
                kept.append(b)
        self.blocks = kept

        if self.blocks:
            last = max((b.end - timedelta(seconds=1)).date() for b in self.blocks)
            last_monday = ensure_monday(last)
            needed = max(1, ((last_monday - self.week_start).days // 7) + 1)
            self.weeks = min(max(needed, self.weeks), 16)

    def set_week1_anchor(self, block_id: str) -> Block:
        """Ustaw poniedziałek osi na tydzień zawierający start bloku."""
        from brudnopis_ot.core.calendar_weeks import ensure_monday

        block = self.get_block(block_id)
        if block is None:
            raise KeyError(f"Brak bloku: {block_id}")

        self.anchor_block_id = block.id
        self.set_axis_monday(ensure_monday(block.start.date()))
        return block

    @property
    def range_start(self) -> datetime:
        return datetime.combine(self.week_start, datetime.min.time())

    @property
    def range_end(self) -> datetime:
        return self.range_start + timedelta(days=7 * self.weeks)

    def sorted_blocks(self) -> list[Block]:
        return sorted(self.blocks, key=lambda b: (b.start, b.end, b.id))

    def add_block(self, block: Block) -> None:
        self.blocks.append(block)

    def remove_block(self, block_id: str) -> bool:
        before = len(self.blocks)
        self.blocks = [b for b in self.blocks if b.id != block_id]
        return len(self.blocks) < before

    def get_block(self, block_id: str) -> Block | None:
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    code: str
    message_pl: str
    related_block_ids: tuple[str, ...] = ()
    underline_range: TimeRange | None = None


@dataclass(frozen=True, slots=True)
class WeeklyRestPeriod:
    """Continuous rest segment classified as a weekly rest candidate."""

    block_ids: tuple[str, ...]
    start: datetime
    end: datetime
    classification: WeeklyRestClass
    started_abroad: bool
    country_start: str | None
    debt_hours: float
    compensation_hours: float
    compensation_precedes_regular: bool
    label: str

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0

    @property
    def is_weekly(self) -> bool:
        return self.classification in ("reduced", "regular")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    findings: tuple[Finding, ...]
    weekly_rests: tuple[WeeklyRestPeriod, ...]
    six_day_windows: tuple[TimeRange, ...]
    compensation_windows: tuple[TimeRange, ...]
    compensation_hints: tuple[str, ...]

    @property
    def has_violations(self) -> bool:
        return any(f.severity == "violation" for f in self.findings)
