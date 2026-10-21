from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ewidentor.core.brudnopis_import import BorderCrossingRow, PlaceRow, RawSegment

# Aktywności liczone pod MiLo (jazda + praca + dyspozycja/dyżur).
MILO_ACTIVITIES = frozenset({"drive", "work", "availability", "other_work"})


@dataclass(slots=True)
class CountryDayMinutes:
    day: date
    country_code: str
    minutes: int
    # Earliest MiLo overlap with this country on day (table sort).
    first_seen: datetime | None = None


def _seg_abs_range(seg: RawSegment) -> tuple[datetime, datetime]:
    start = datetime.combine(seg.day, datetime.min.time()) + timedelta(
        minutes=seg.start_minute
    )
    end = datetime.combine(seg.day, datetime.min.time()) + timedelta(
        minutes=seg.end_minute
    )
    return start, end


def _country_intervals_from_crossings(
    crossings: list[BorderCrossingRow],
    *,
    horizon_end: datetime | None,
) -> list[tuple[datetime, datetime, str]]:
    """
    Po crossing i: kraj = country_entered aż do następnego crossing.
    Przed pierwszym crossingiem — brak kraju (pomijamy).
    """
    if not crossings:
        return []
    ordered = sorted(crossings, key=lambda c: c.occurred_at)
    end = horizon_end or (ordered[-1].occurred_at + timedelta(days=2))
    intervals: list[tuple[datetime, datetime, str]] = []
    for i, c in enumerate(ordered):
        country = (c.country_entered or "").strip().upper()
        if not country:
            continue
        t0 = c.occurred_at
        t1 = ordered[i + 1].occurred_at if i + 1 < len(ordered) else end
        if t1 <= t0:
            continue
        intervals.append((t0, t1, country))
    return intervals


def _country_intervals_from_places(
    places: list[PlaceRow],
    *,
    horizon_end: datetime | None,
) -> list[tuple[datetime, datetime, str]]:
    """
    Od place i: kraj = country_code aż do następnego place.
    START/END z tym samym narodem tworzą ciągły przedział.
    """
    if not places:
        return []
    ordered = sorted(places, key=lambda p: p.occurred_at)
    end = horizon_end or (ordered[-1].occurred_at + timedelta(days=2))
    intervals: list[tuple[datetime, datetime, str]] = []
    for i, p in enumerate(ordered):
        country = (p.country_code or "").strip().upper()
        if not country:
            continue
        t0 = p.occurred_at
        t1 = ordered[i + 1].occurred_at if i + 1 < len(ordered) else end
        if t1 <= t0:
            continue
        intervals.append((t0, t1, country))
    return intervals


def _overlap_minutes(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> int:
    start = max(a0, b0)
    end = min(a1, b1)
    if end <= start:
        return 0
    return int((end - start).total_seconds() // 60)


def _minutes_from_intervals(
    segments: list[RawSegment],
    intervals: list[tuple[datetime, datetime, str]],
) -> list[CountryDayMinutes]:
    if not segments or not intervals:
        return []
    totals: dict[tuple[date, str], int] = {}
    first_seen: dict[tuple[date, str], datetime] = {}
    for seg in segments:
        act = (seg.activity or "").strip().lower()
        if act not in MILO_ACTIVITIES:
            continue
        s0, s1 = _seg_abs_range(seg)
        if s1 <= s0:
            continue
        for c0, c1, country in intervals:
            mins = _overlap_minutes(s0, s1, c0, c1)
            if mins <= 0:
                continue
            key = (seg.day, country)
            totals[key] = totals.get(key, 0) + mins
            overlap_start = max(s0, c0)
            prev = first_seen.get(key)
            if prev is None or overlap_start < prev:
                first_seen[key] = overlap_start

    def sort_key(item: tuple[tuple[date, str], int]) -> tuple:
        (day, code), _mins = item
        seen = first_seen.get((day, code))
        return (day, seen or datetime.max, code)

    return [
        CountryDayMinutes(
            day=day,
            country_code=code,
            minutes=mins,
            first_seen=first_seen.get((day, code)),
        )
        for (day, code), mins in sorted(totals.items(), key=sort_key)
        if mins > 0
    ]


def presence_first_seen_map(
    derived: list[CountryDayMinutes],
) -> dict[tuple[date, str], datetime]:
    """Map (day, ISO-2) → earliest presence start for MiLo table ordering."""
    out: dict[tuple[date, str], datetime] = {}
    for item in derived:
        if item.first_seen is None:
            continue
        key = (item.day, item.country_code.upper())
        prev = out.get(key)
        if prev is None or item.first_seen < prev:
            out[key] = item.first_seen
    return out


def country_labels_from_places(places: list[PlaceRow]) -> dict[date, str]:
    """Etykiety „Kraje (DDD)” — unikalne nation w kolejności pierwszego place w dniu."""
    by_day: dict[date, list[str]] = {}
    for p in sorted(places, key=lambda x: x.occurred_at):
        code = (p.country_code or "").strip().upper()
        if not code:
            continue
        day = p.occurred_at.date()
        seen = by_day.setdefault(day, [])
        if code not in seen:
            seen.append(code)
    return {day: ", ".join(codes) for day, codes in sorted(by_day.items())}


def derive_country_minutes(
    segments: list[RawSegment],
    crossings: list[BorderCrossingRow],
    places: list[PlaceRow] | None = None,
) -> list[CountryDayMinutes]:
    """
    Przecięcie aktywności MiLo z przedziałami kraju.
    Preferuj border_crossings; gdy puste — places (START/END).
    """
    if not segments:
        return []
    last_day = max(s.day for s in segments)
    horizon = datetime.combine(last_day + timedelta(days=1), datetime.min.time())
    if crossings:
        intervals = _country_intervals_from_crossings(crossings, horizon_end=horizon)
    elif places:
        intervals = _country_intervals_from_places(places, horizon_end=horizon)
    else:
        return []
    return _minutes_from_intervals(segments, intervals)
