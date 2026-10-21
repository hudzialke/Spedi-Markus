from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timedelta

from ewidentor.core.brudnopis_import import RawSegment
from ewidentor.core.display_tz import DEFAULT_ZONE, DisplayZone, utc_naive_to_zone
from ewidentor.core.holidays_pl import (
    is_polish_holiday,
    is_sunday_or_polish_holiday,
    norma_kp_minutes,
)
from ewidentor.core.models import (
    ABSENCE_LABELS_PL,
    DUTY_MODE_HEURISTIC,
    DUTY_MODE_SPAN,
    Absence,
    AbsenceKind,
    DayCard,
    Driver,
    MonthCard,
    normalize_duty_mode,
)

# Heurystyka dyspozycji: szum / mylnie oznaczony odpoczynek / krótka dyspozycja → praca.
_AVAIL_NOISE_MAX = 15  # min — pomijamy
_AVAIL_AS_WORK_MAX = 3 * 60  # ≤3 h między jazdą/pracą → other_work
_AVAIL_AS_REST_MIN = 9 * 60  # ≥9 h ciągłej dyspozycji → rest (nie dyżur)


def format_minutes(total_min: int) -> str:
    hours, minutes = divmod(max(0, int(total_min)), 60)
    return f"{hours}:{minutes:02d}"


def format_clock(minute: int | None) -> str:
    """Format minutes-from-midnight as HH:MM (no timezone conversion)."""
    if minute is None:
        return ""
    total = max(0, int(minute))
    if total == 24 * 60:
        return "24:00"
    h, m = divmod(total % (24 * 60), 60)
    return f"{h:02d}:{m:02d}"


def format_clock_local(
    day: date,
    minute: int | None,
    zone: DisplayZone = DEFAULT_ZONE,
) -> str:
    """UTC card day + minute → HH:MM in *zone* (jak Brudnopis OT)."""
    if minute is None:
        return ""
    utc_dt = datetime.combine(day, time.min) + timedelta(minutes=max(0, int(minute)))
    local = utc_naive_to_zone(utc_dt, zone)
    if zone == "UTC" and int(minute) == 24 * 60:
        return "24:00"
    return local.strftime("%H:%M")


def is_sunday_or_holiday(day: date) -> bool:
    return is_sunday_or_polish_holiday(day)


def monthly_norma_minutes(year: int, month: int, employment_fraction: float = 1.0) -> int:
    """Wymiar KP art. 130 dla miesiąca (minuty)."""
    minutes, _ = norma_kp_minutes(year, month, employment_fraction=employment_fraction)
    return minutes


def _overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    start = max(a0, b0)
    end = min(a1, b1)
    return max(0, end - start)


def _night_overlap_minutes(
    start_min: int,
    end_min: int,
    night_start: time,
    night_end: time,
) -> int:
    """Overlap of [start_min, end_min) within day with night window (same calendar day)."""
    ns = night_start.hour * 60 + night_start.minute
    ne = night_end.hour * 60 + night_end.minute
    if ne <= ns:
        # okno przez północ: [ns, 1440) U [0, ne)
        return _overlap(start_min, end_min, ns, 1440) + _overlap(start_min, end_min, 0, ne)
    return _overlap(start_min, end_min, ns, ne)


def _map_activity(activity: str) -> str:
    key = (activity or "").strip().lower()
    if key in {"drive", "jazda"}:
        return "drive"
    if key in {"work", "praca", "other_work"}:
        return "work"
    if key in {"availability", "available", "dyspozycja", "duty"}:
        return "availability"
    if key in {"rest", "card_out", "postoj"}:
        return "rest"
    return "other"


def _day_has_drive_or_work(segments: list[RawSegment], day: date) -> bool:
    for seg in segments:
        if seg.day != day:
            continue
        kind = _map_activity(seg.activity)
        if kind in {"drive", "work"}:
            return True
    return False


def build_day_from_segments(
    day: date,
    segments: list[RawSegment],
    *,
    night_start: time,
    night_end: time,
    plan_min: int,
    night_on_availability: bool = False,
    duty_mode: str = "raw",
) -> DayCard:
    card = DayCard(day=day, plan_min=plan_min)
    workish_starts: list[int] = []
    workish_ends: list[int] = []
    duty_heuristic = normalize_duty_mode(duty_mode) == DUTY_MODE_HEURISTIC
    duty_span = normalize_duty_mode(duty_mode) == DUTY_MODE_SPAN
    has_workish = _day_has_drive_or_work(segments, day) if (duty_heuristic or duty_span) else False
    rest_spans: list[tuple[int, int]] = []

    for seg in segments:
        if seg.day != day:
            continue
        kind = _map_activity(seg.activity)
        start = max(0, min(1440, seg.start_minute))
        end = max(0, min(1440, seg.end_minute))
        if end <= start:
            continue
        minutes = end - start
        if kind == "drive":
            card.drive_min += minutes
            card.night_min += _night_overlap_minutes(start, end, night_start, night_end)
            workish_starts.append(start)
            workish_ends.append(end)
        elif kind == "work":
            card.other_work_min += minutes
            card.night_min += _night_overlap_minutes(start, end, night_start, night_end)
            workish_starts.append(start)
            workish_ends.append(end)
        elif kind == "availability":
            card.availability_min += minutes
            mapped = "duty" if (duty_heuristic or duty_span) else "skip"
            if duty_heuristic:
                if minutes <= _AVAIL_NOISE_MAX:
                    mapped = "skip"
                elif minutes >= _AVAIL_AS_REST_MIN:
                    mapped = "rest"
                elif has_workish and minutes <= _AVAIL_AS_WORK_MAX:
                    # Krótka dyspozycja w dniu z jazdą/pracą → często załadunek jako other_work.
                    mapped = "work"
                else:
                    mapped = "duty" if (duty_heuristic or duty_span) else "skip"

            if mapped == "skip":
                pass
            elif mapped == "rest":
                card.rest_min += minutes
                if minutes >= 9 * 60:
                    card.daily_rest_min = max(card.daily_rest_min, minutes)
            elif mapped == "work":
                card.other_work_min += minutes
                card.night_min += _night_overlap_minutes(
                    start, end, night_start, night_end
                )
                workish_starts.append(start)
                workish_ends.append(end)
            else:
                card.duty_50_min += minutes
                if night_on_availability:
                    card.night_min += _night_overlap_minutes(
                        start, end, night_start, night_end
                    )
                workish_starts.append(start)
                workish_ends.append(end)
        elif kind == "rest":
            card.rest_min += minutes
            rest_spans.append((start, end))
            if minutes >= 9 * 60:
                card.daily_rest_min = max(card.daily_rest_min, minutes)

    card.work_min = card.drive_min + card.other_work_min

    if duty_span and has_workish and rest_spans and workish_starts and workish_ends:
        span_lo = min(workish_starts)
        span_hi = max(workish_ends)
        kept_rest = 0
        longest_rest = 0
        for rs, re in rest_spans:
            minutes = re - rs
            longest_rest = max(longest_rest, minutes)
            interstitial = rs >= span_lo and re <= span_hi
            if not interstitial:
                kept_rest += minutes
                continue
            if minutes <= 15:
                card.break_15_min += minutes
                card.work_min += minutes
            else:
                card.duty_50_min += minutes
                if night_on_availability:
                    card.night_min += _night_overlap_minutes(
                        rs, re, night_start, night_end
                    )
                    workish_starts.append(rs)
                    workish_ends.append(re)
        card.rest_min = kept_rest
        card.daily_rest_min = longest_rest if longest_rest >= 9 * 60 else card.daily_rest_min

    if duty_span and card.work_min >= 6 * 60 and card.break_15_min == 0:
        card.break_15_min = 15
        card.work_min += 15

    if workish_starts and workish_ends:
        card.start_min = min(workish_starts)
        card.end_min = max(workish_ends)
        if card.end_min == 1440:
            card.end_min = 24 * 60  # pokazuj 24:00

    if is_sunday_or_holiday(day):
        card.sunday_holiday_min = card.work_min

    # Czas płatny = czas pracy (dyżur jest osobną linią pensji).
    card.paid_min = card.work_min
    if card.plan_min and card.work_min > card.plan_min:
        card.overtime_min = card.work_min - card.plan_min
    return card


def apply_absences(days: list[DayCard], absences: list[Absence]) -> None:
    by_day: dict[date, list[Absence]] = {}
    for a in absences:
        by_day.setdefault(a.day, []).append(a)
    for card in days:
        items = by_day.get(card.day, [])
        if not items:
            continue
        labels = []
        for a in items:
            label = a.title or ABSENCE_LABELS_PL.get(a.kind, a.kind.value)
            labels.append(label)
            card.absence_kind = a.kind
            if a.kind in {
                AbsenceKind.SICK,
                AbsenceKind.PAID_LEAVE,
                AbsenceKind.UNPAID_LEAVE,
                AbsenceKind.OTHER_JUSTIFIED,
                AbsenceKind.UNJUSTIFIED,
                AbsenceKind.FREE_DAY,
            }:
                # nieobecność dnia — czyścimy czas pracy z tacho (ewidencja nieobecności)
                if a.minutes <= 0:
                    card.work_min = 0
                    card.drive_min = 0
                    card.other_work_min = 0
                    card.duty_50_min = 0
                    card.duty_100_min = 0
                    card.availability_min = 0
                    card.night_min = 0
                    card.sunday_holiday_min = 0
                    card.overtime_min = 0
                    card.paid_min = 0
                    card.start_min = None
                    card.end_min = None
                    card.absence_label = label
                else:
                    card.note = (card.note + "; " if card.note else "") + f"{label} {a.minutes} min"
        if labels and not card.absence_label:
            card.absence_label = ", ".join(labels)


def build_month_card(
    driver: Driver,
    year: int,
    month: int,
    segments: list[RawSegment],
    absences: list[Absence],
    *,
    manual_days: set[date] | None = None,
) -> MonthCard:
    days_in_month = monthrange(year, month)[1]
    norma = monthly_norma_minutes(year, month, driver.employment_fraction)
    segs_by_day: dict[date, list[RawSegment]] = {}
    for s in segments:
        segs_by_day.setdefault(s.day, []).append(s)
    manual = manual_days or set()

    days: list[DayCard] = []
    for d in range(1, days_in_month + 1):
        day = date(year, month, d)
        plan = 0
        if day.weekday() < 5 and not is_polish_holiday(day):
            plan = int(8 * 60 * driver.employment_fraction)
        card = build_day_from_segments(
            day,
            segs_by_day.get(day, []),
            night_start=driver.pay.night_start,
            night_end=driver.pay.night_end,
            plan_min=plan,
            night_on_availability=bool(driver.pay.night_on_availability),
            duty_mode=driver.pay.duty_mode or "raw",
        )
        if day in manual:
            card.is_manual = True
        days.append(card)

    apply_absences(days, absences)

    # Nadgodziny miesięczne vs norma (po absencjach)
    total_work = sum(c.work_min for c in days)
    monthly_ot = max(0, total_work - norma)
    # Rozłóż proporcjonalnie tylko informacyjnie na dni z przekroczeniem planu
    if monthly_ot > 0:
        for c in days:
            if c.work_min > c.plan_min > 0:
                c.overtime_min = c.work_min - c.plan_min
            elif c.plan_min == 0 and c.work_min > 0:
                c.overtime_min = c.work_min  # praca w niedzielę/święto — do ewidencji

    return MonthCard(
        driver=driver,
        year=year,
        month=month,
        days=days,
        norma_min=norma,
        employer_name=driver.employer_name,
    )


def sum_field(days: list[DayCard], attr: str) -> int:
    return sum(int(getattr(d, attr)) for d in days)


def week_slices(year: int, month: int) -> list[tuple[date, date]]:
    """Kalendarzowe tygodnie Pn–Nd przecinające miesiąc."""
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    # poniedziałek tygodnia zawierającego 1. dzień
    start = first - timedelta(days=first.weekday())
    out: list[tuple[date, date]] = []
    cur = start
    while cur <= last:
        end = cur + timedelta(days=6)
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out
