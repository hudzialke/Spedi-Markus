from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date, datetime, timedelta


DAY_LABELS_PL = tuple(_tr(day) for day in ("Pn", "Wt", "Śr", "Czw", "Pt", "Sb", "Nd"))


def ensure_monday(d: date) -> date:
    """Return Monday of the ISO week containing d."""
    return d - timedelta(days=d.weekday())


def week_bounds(monday: date) -> tuple[datetime, datetime]:
    """Calendar week: Monday 00:00 inclusive → next Monday 00:00 exclusive (art. 4 lit. i)."""
    if monday.weekday() != 0:
        raise ValueError("monday must be a Monday")
    start = datetime.combine(monday, datetime.min.time())
    end = start + timedelta(days=7)
    return start, end


def week_index(scenario_monday: date, instant: datetime) -> int:
    """0-based week index relative to scenario Monday; may be negative or beyond range."""
    start = datetime.combine(scenario_monday, datetime.min.time())
    delta = instant - start
    return int(delta.total_seconds() // (7 * 24 * 3600))


def week_end_deadline(week_monday: date) -> datetime:
    """End of calendar week (Sunday 24:00 = next Monday 00:00)."""
    _, end = week_bounds(week_monday)
    return end


def third_week_end_after(ot_start: datetime, scenario_monday: date | None = None) -> datetime:
    """
    Deadline for compensation (art. 8(6b)): before the end of the third week
    following the calendar week in which the reduced weekly rest occurred.

    Example: reduced OT in week Mon 29.06 → following weeks are 06.07, 13.07, 20.07;
    deadline = Sun 26.07 24:00 = Mon 27.07 00:00.
    """
    del scenario_monday  # kept for call-site compatibility
    ot_week_monday = ensure_monday(ot_start.date())
    third_following_monday = ot_week_monday + timedelta(weeks=3)
    return week_end_deadline(third_following_monday)


def format_week_label(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%d.%m.%Y')} – {sunday.strftime('%d.%m.%Y')}"


def format_duration_hm(hours: float) -> str:
    total_minutes = int(round(hours * 60))
    h, m = divmod(abs(total_minutes), 60)
    sign = "-" if total_minutes < 0 else ""
    return f"{sign}{h}:{m:02d}"
