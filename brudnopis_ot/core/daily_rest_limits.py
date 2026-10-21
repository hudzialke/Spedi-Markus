from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from brudnopis_ot.core.calendar_weeks import week_index
from brudnopis_ot.core.daily_summary import format_minutes
from brudnopis_ot.core.models import Finding, Scenario, TimeRange, WeeklyRestPeriod

# 561/2006 daily rest (art. 8)
# - daily rest of at least 9 hours is allowed as a "reduced daily rest"
# - max 3 reduced daily rests between two weekly rest periods
# - reduced daily rest below 11h requires compensation (handled elsewhere later)
DAILY_REDUCED_MIN_H = 9.0
DAILY_REDUCED_NORMAL_MIN_H = 11.0
MAX_REDUCED_DAILY_BETWEEN_WEEKLY = 3


@dataclass(frozen=True, slots=True)
class DailyReduced9hResult:
    findings: tuple[Finding, ...]
    hints: tuple[str, ...]


def _daily_reduced_periods(weekly_all: list[WeeklyRestPeriod]) -> list[WeeklyRestPeriod]:
    # weekly_all contains weekly rests + all pure REST segments classified as:
    # - "regular" / "reduced" weekly OT
    # - "too_short" for daily rests (including OD >= 9h)
    out: list[WeeklyRestPeriod] = []
    for p in weekly_all:
        if p.classification != "too_short":
            continue
        hours = p.duration_hours
        if DAILY_REDUCED_MIN_H - 1e-6 <= hours < DAILY_REDUCED_NORMAL_MIN_H - 1e-6:
            out.append(p)
    return sorted(out, key=lambda p: p.start)


def _week_start_dt(scenario: Scenario, widx: int) -> datetime:
    monday = scenario.week_start + timedelta(days=7 * widx)
    return datetime.combine(monday, datetime.min.time())


def _intervals_between_weekly_rests(
    scenario: Scenario,
    weekly: list[WeeklyRestPeriod],
    last_known_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """
    Intervals where "max 3 reduced daily rests" applies:
    between consecutive weekly rests (OT, regular/reduced), plus an open interval
    after the last weekly rest until end of the visible/known scenario.
    """
    if not weekly:
        # No weekly rest periods at all — we can't anchor "between two weekly rests".
        # Return a single open interval starting at scenario start so we can still show hints.
        return [(scenario.range_start, last_known_end)]

    intervals: list[tuple[datetime, datetime]] = []
    for a, b in zip(weekly, weekly[1:], strict=False):
        intervals.append((a.end, b.start))

    # Open interval after last weekly rest
    intervals.append((weekly[-1].end, last_known_end))
    return intervals


def analyze_daily_reduced_9h_limits(
    scenario: Scenario,
    weekly_all: list[WeeklyRestPeriod],
    weekly: list[WeeklyRestPeriod],
) -> DailyReduced9hResult:
    # Use last known end so we don't pretend future blocks exist.
    last_known_end = max((b.end for b in scenario.blocks), default=scenario.range_end)

    daily_reduced = _daily_reduced_periods(weekly_all)
    intervals = _intervals_between_weekly_rests(scenario, weekly, last_known_end)

    findings: list[Finding] = []
    hints: list[str] = []

    # Precompute daily reduced counts per interval
    interval_used_total: list[int] = []
    for start, end in intervals:
        used = sum(1 for p in daily_reduced if p.start >= start and p.start < end)
        interval_used_total.append(used)
        if used > MAX_REDUCED_DAILY_BETWEEN_WEEKLY:
            hints_range = TimeRange(start=start, end=end)
            findings.append(
                Finding(
                    severity="violation",
                    code="daily_9h_limit",
                    message_pl=(
                        "Dobowy odpoczynek skrócony do 9-<11h: "
                        f"między odpoczynkami tygodniowymi wykorzystano {used} "
                        f"(limit {MAX_REDUCED_DAILY_BETWEEN_WEEKLY})."
                    ),
                    underline_range=hints_range,
                )
            )

    # Map "remaining" into calendar weeks, using the interval that contains the week start.
    for widx in range(scenario.weeks):
        wstart = _week_start_dt(scenario, widx)
        wend = wstart + timedelta(days=7)

        # Pick interval by overlap with the calendar week
        interval_idx = None
        for i, (start, end) in enumerate(intervals):
            if max(wstart, start) < min(wend, end):
                interval_idx = i
                break
        if interval_idx is None:
            continue

        used_so_far = sum(
            1
            for p in daily_reduced
            if p.start >= intervals[interval_idx][0] and p.start < min(wend, last_known_end)
        )
        remaining = MAX_REDUCED_DAILY_BETWEEN_WEEKLY - used_so_far
        remaining = max(0, remaining)

        hints.append(
            "W tygodniu "
            f"{widx + 1}: skrócony dobowy OD 9-<11h - "
            f"wykorzystane {used_so_far}/{MAX_REDUCED_DAILY_BETWEEN_WEEKLY}, "
            f"jeszcze {remaining}×."
        )

    return DailyReduced9hResult(
        findings=tuple(findings),
        hints=tuple(hints),
    )

