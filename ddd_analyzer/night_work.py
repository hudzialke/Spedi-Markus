from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ddd_analyzer.display_tz import (
    DEFAULT_ZONE,
    DisplayZone,
    utc_minutes_to_zone,
    utc_naive_to_zone,
)
from ddd_analyzer.models import ActivitySegment, ActivityType, ContinuousRestBlock
from ddd_analyzer.rest_timeline import continuous_rest_blocks

H9 = 9 * 60
H10 = 10 * 60

WORK_ACTIVITIES = frozenset({ActivityType.DRIVE, ActivityType.WORK})


@dataclass(frozen=True, slots=True)
class NightWindow:
    """Pora nocna wg ustawy o czasie pracy kierowcow (domyslnie 00:00-04:00)."""

    start_minute: int = 0
    end_minute: int = 4 * 60

    @property
    def label(self) -> str:
        from ddd_analyzer.summarize import format_clock

        return f"{format_clock(self.start_minute)}–{format_clock(self.end_minute)}"


@dataclass(slots=True)
class NightWorkPeriod:
    """Okres pracy miedzy odpoczynkami dobowymi/tygodniowymi (>=9h)."""

    start: datetime
    end: datetime
    work_min: int
    night_work_min: int
    night_limited: bool
    exceeded_limit: bool
    limit_min: int = H10

    @property
    def over_min(self) -> int:
        if not self.exceeded_limit:
            return 0
        return max(0, self.work_min - self.limit_min)


@dataclass(slots=True)
class NightWorkAnalysis:
    night_window: NightWindow
    periods: list[NightWorkPeriod]
    violations: list[NightWorkPeriod]

    @property
    def has_violations(self) -> bool:
        return bool(self.violations)

    @property
    def night_limited_count(self) -> int:
        return sum(1 for p in self.periods if p.night_limited)


def _overlap_minutes(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> int:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end <= start:
        return 0
    return int((end - start).total_seconds() // 60)


def _night_bounds(day: date, window: NightWindow) -> tuple[datetime, datetime]:
    """Midnight of *day* is already in the analysis display zone (naive wall)."""
    base = datetime.combine(day, datetime.min.time())
    return (
        base + timedelta(minutes=window.start_minute),
        base + timedelta(minutes=window.end_minute),
    )


def _work_overlap_minutes(
    start: datetime,
    end: datetime,
    window: NightWindow,
) -> int:
    total = 0
    day = start.date()
    last_day = end.date()
    while day <= last_day:
        night_start, night_end = _night_bounds(day, window)
        total += _overlap_minutes(start, end, night_start, night_end)
        day += timedelta(days=1)
    return total


def _qualifying_rests(
    segments: list[ActivitySegment],
    zone: DisplayZone,
) -> list[ContinuousRestBlock]:
    blocks: list[ContinuousRestBlock] = []
    for block in continuous_rest_blocks(segments):
        if block.duration_min < H9:
            continue
        blocks.append(
            ContinuousRestBlock(
                start=utc_naive_to_zone(block.start, zone),
                end=utc_naive_to_zone(block.end, zone),
            )
        )
    return blocks


def _work_segments(
    segments: list[ActivitySegment],
    zone: DisplayZone,
) -> list[tuple[datetime, datetime, int]]:
    items: list[tuple[datetime, datetime, int]] = []
    for segment in sorted(segments, key=lambda s: (s.day, s.start_minute)):
        if segment.activity not in WORK_ACTIVITIES:
            continue
        start, end = utc_minutes_to_zone(
            segment.day, segment.start_minute, segment.end_minute, zone
        )
        items.append((start, end, segment.duration_min))
    return items


def _period_work(
    period_start: datetime,
    period_end: datetime,
    work_items: list[tuple[datetime, datetime, int]],
    window: NightWindow,
) -> tuple[int, int]:
    total = 0
    night = 0
    for start, end, _duration in work_items:
        overlap = _overlap_minutes(start, end, period_start, period_end)
        if overlap <= 0:
            continue
        clipped_start = max(start, period_start)
        clipped_end = min(end, period_end)
        total += overlap
        night += _work_overlap_minutes(clipped_start, clipped_end, window)
    return total, night


def analyze_night_work(
    segments: list[ActivitySegment],
    *,
    night_window: NightWindow | None = None,
    zone: DisplayZone = DEFAULT_ZONE,
) -> NightWorkAnalysis:
    """
    Ustawa o czasie pracy kierowcow, art. 21:
    przy pracy w porze nocnej czas pracy (jazda + inna praca) miedzy odpoczynkami
    dobowymi/tygodniowymi nie moze przekroczyc 10 godzin.

    Segmenty karty sa w UTC; pora nocna 00:00–04:00 jest liczona w *zone*
    (domyslnie Europe/Warsaw).
    """
    window = night_window or NightWindow()
    rests = _qualifying_rests(segments, zone)
    work_items = _work_segments(segments, zone)
    if not work_items:
        return NightWorkAnalysis(night_window=window, periods=[], violations=[])

    periods: list[NightWorkPeriod] = []

    def append_period(start: datetime, end: datetime) -> None:
        if end <= start:
            return
        work_min, night_work_min = _period_work(start, end, work_items, window)
        if work_min <= 0:
            return
        night_limited = night_work_min > 0
        exceeded = night_limited and work_min > H10
        periods.append(
            NightWorkPeriod(
                start=start,
                end=end,
                work_min=work_min,
                night_work_min=night_work_min,
                night_limited=night_limited,
                exceeded_limit=exceeded,
            )
        )

    if rests:
        first_work_start = min(start for start, _end, _dur in work_items)
        if first_work_start < rests[0].start:
            append_period(first_work_start, rests[0].start)

        for idx, rest in enumerate(rests):
            period_start = rest.end
            period_end = rests[idx + 1].start if idx + 1 < len(rests) else max(
                end for _start, end, _dur in work_items
            )
            append_period(period_start, period_end)
    else:
        append_period(
            min(start for start, _end, _dur in work_items),
            max(end for _start, end, _dur in work_items),
        )

    violations = [p for p in periods if p.exceeded_limit]
    return NightWorkAnalysis(night_window=window, periods=periods, violations=violations)
