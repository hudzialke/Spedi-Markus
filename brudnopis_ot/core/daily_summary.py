from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from brudnopis_ot.core.models import ActivityKind, Block, Scenario


@dataclass(slots=True)
class DayTotals:
    day: date
    drive_min: int = 0
    work_min: int = 0
    availability_min: int = 0
    rest_min: int = 0

    @property
    def total_min(self) -> int:
        return self.drive_min + self.work_min + self.availability_min + self.rest_min


def format_minutes(total_min: int) -> str:
    hours, minutes = divmod(max(0, total_min), 60)
    return f"{hours}:{minutes:02d}"


def _overlap_minutes(block: Block, day: date) -> int:
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    start = max(block.start, day_start)
    end = min(block.end, day_end)
    if end <= start:
        return 0
    return int((end - start).total_seconds() // 60)


def build_daily_totals(
    blocks: list[Block],
    *,
    range_start: date | None = None,
    range_end: date | None = None,
) -> list[DayTotals]:
    """Sum activity minutes per calendar day from scenario blocks."""
    if not blocks:
        return []

    first = min(b.start.date() for b in blocks)
    last = max((b.end - timedelta(seconds=1)).date() for b in blocks)
    if range_start is not None:
        first = max(first, range_start)
    if range_end is not None:
        last = min(last, range_end)
    if last < first:
        return []

    by_day: dict[date, DayTotals] = {}
    day = first
    while day <= last:
        by_day[day] = DayTotals(day=day)
        day += timedelta(days=1)

    for block in blocks:
        d0 = block.start.date()
        d1 = (block.end - timedelta(seconds=1)).date()
        cur = d0
        while cur <= d1:
            if cur not in by_day:
                cur += timedelta(days=1)
                continue
            minutes = _overlap_minutes(block, cur)
            totals = by_day[cur]
            if block.kind == ActivityKind.DRIVE:
                totals.drive_min += minutes
            elif block.kind == ActivityKind.WORK:
                totals.work_min += minutes
            elif block.kind == ActivityKind.AVAILABILITY:
                totals.availability_min += minutes
            elif block.kind in (ActivityKind.REST, ActivityKind.CARD_OUT):
                totals.rest_min += minutes
            # CREW ignored for daily activity totals
            cur += timedelta(days=1)

    return [by_day[d] for d in sorted(by_day)]


def totals_for_visible_scenario(scenario: Scenario) -> list[DayTotals]:
    start = scenario.week_start
    end = start + timedelta(days=7 * scenario.weeks - 1)
    return build_daily_totals(scenario.blocks, range_start=start, range_end=end)


def sum_totals(days: list[DayTotals]) -> DayTotals:
    out = DayTotals(day=days[0].day if days else date.today())
    for d in days:
        out.drive_min += d.drive_min
        out.work_min += d.work_min
        out.availability_min += d.availability_min
        out.rest_min += d.rest_min
    return out
