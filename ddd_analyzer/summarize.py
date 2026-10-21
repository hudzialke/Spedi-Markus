from __future__ import annotations

from datetime import date, datetime, timedelta

from ddd_analyzer.display_tz import DEFAULT_ZONE, DisplayZone, utc_minutes_to_zone
from ddd_analyzer.models import ActivitySegment, ActivityType, DailySummary, WeekSummary


def build_daily_summaries(
    segments: list[ActivitySegment],
    odometer_by_day: dict[date, int | None] | None = None,
) -> list[DailySummary]:
    by_day: dict[date, DailySummary] = {}
    for segment in segments:
        summary = by_day.setdefault(segment.day, DailySummary(day=segment.day))
        minutes = segment.duration_min
        if segment.activity == ActivityType.DRIVE:
            summary.drive_min += minutes
        elif segment.activity == ActivityType.WORK:
            summary.work_min += minutes
        elif segment.activity == ActivityType.AVAILABILITY:
            summary.availability_min += minutes
        else:
            summary.rest_min += minutes

    if odometer_by_day:
        for day, odometer in odometer_by_day.items():
            summary = by_day.setdefault(day, DailySummary(day=day))
            summary.odometer_km = odometer

    return sorted(by_day.values(), key=lambda s: s.day)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_week_summary(daily_summaries: list[DailySummary], anchor_day: date) -> WeekSummary:
    start = week_start(anchor_day)
    end = start + timedelta(days=6)
    days = [d for d in daily_summaries if start <= d.day <= end]
    return WeekSummary(week_start=start, days=days)


def format_minutes(total_min: int) -> str:
    hours, minutes = divmod(max(0, total_min), 60)
    return f"{hours}:{minutes:02d}"


def format_clock(minute_of_day: int) -> str:
    minute_of_day = max(0, min(minute_of_day, 24 * 60))
    if minute_of_day >= 24 * 60:
        return "24:00"
    hours, minutes = divmod(minute_of_day, 60)
    return f"{hours:02d}:{minutes:02d}"


def segment_time_label(
    segment: ActivitySegment,
    zone: DisplayZone = DEFAULT_ZONE,
) -> tuple[str, str, str]:
    """Zwraca (od, do, czas trwania); czasy w wybranej strefie (karta = UTC)."""
    start, end = utc_minutes_to_zone(
        segment.day, segment.start_minute, segment.end_minute, zone
    )

    def _fmt(dt: datetime) -> str:
        if dt.date() != segment.day and zone != "UTC":
            return dt.strftime("%d.%m %H:%M")
        return dt.strftime("%H:%M")

    return (_fmt(start), _fmt(end), format_minutes(segment.duration_min))



def segments_for_day(segments: list[ActivitySegment], day: date) -> list[ActivitySegment]:
    return [s for s in segments if s.day == day]
