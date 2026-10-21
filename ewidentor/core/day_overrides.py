"""Lokalne nadpisania dni aktywności (Ewidentor) — bez zapisu do Brudnopisu."""

from __future__ import annotations

from spedi.i18n import tr as _tr

from datetime import date, datetime, time, timedelta

from ewidentor.core.brudnopis_import import RawSegment
from ewidentor.core.display_tz import (
    AmbiguousLocalTimeError,
    DisplayZone,
    NonexistentLocalTimeError,
    local_time_candidates,
)
from ewidentor.core.month_card import format_clock, format_minutes

ACTIVITY_LABELS_PL: dict[str, str] = {
    "drive": _tr("Jazda"),
    "work": _tr("Praca"),
    "availability": "Dyspozycja",
    "rest": _tr("Odpoczynek"),
}

ACTIVITY_KEYS = tuple(ACTIVITY_LABELS_PL.keys())


def normalize_activity(activity: str) -> str:
    key = (activity or "").strip().lower()
    if key in {"drive", "jazda"}:
        return "drive"
    if key in {"work", "praca", "other_work"}:
        return "work"
    if key in {"availability", "available", "dyspozycja", "duty"}:
        return "availability"
    if key in {"rest", "card_out", "postoj", "odpoczynek"}:
        return "rest"
    return "rest"


def segments_from_override_dicts(
    day: date, items: list[dict]
) -> list[RawSegment]:
    out: list[RawSegment] = []
    for item in items:
        start = int(item.get("start_minute", 0))
        end = int(item.get("end_minute", 0))
        if end <= start:
            continue
        out.append(
            RawSegment(
                day=day,
                activity=normalize_activity(str(item.get("activity", "rest"))),
                start_minute=max(0, min(1440, start)),
                end_minute=max(0, min(1440, end)),
            )
        )
    out.sort(key=lambda s: (s.start_minute, s.end_minute))
    return out


def override_dicts_from_segments(segments: list[RawSegment]) -> list[dict]:
    return [
        {
            "activity": normalize_activity(s.activity),
            "start_minute": int(s.start_minute),
            "end_minute": int(s.end_minute),
        }
        for s in segments
    ]


def apply_day_overrides(
    segments: list[RawSegment],
    overrides: dict[date, list[dict]],
) -> list[RawSegment]:
    """Dni z override: usuń segmenty Brudnopisu, dołóż ręczne."""
    if not overrides:
        return list(segments)
    kept = [s for s in segments if s.day not in overrides]
    for day, items in overrides.items():
        kept.extend(segments_from_override_dicts(day, items))
    kept.sort(key=lambda s: (s.day, s.start_minute, s.end_minute))
    return kept


def summarize_day_minutes(segments: list[RawSegment]) -> dict[str, int]:
    totals = {k: 0 for k in ACTIVITY_KEYS}
    for s in segments:
        key = normalize_activity(s.activity)
        if key in totals:
            totals[key] += max(0, s.end_minute - s.start_minute)
    return totals


def format_day_summary(segments: list[RawSegment]) -> str:
    t = summarize_day_minutes(segments)
    return (
        f"Jazda {format_minutes(t['drive'])} · "
        f"Praca {format_minutes(t['work'])} · "
        f"Dyspozycja {format_minutes(t['availability'])} · "
        f"Odpoczynek {format_minutes(t['rest'])}"
    )


def parse_hm(text: str) -> tuple[int, int] | None:
    raw = (text or "").strip().replace(".", ":")
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if h == 24 and m == 0:
        return 24, 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def local_hm_to_utc_minute(
    day: date, text: str, zone: DisplayZone, *, fold: int | None = None
) -> int | None:
    """HH:MM w strefie wyświetlania → minuta UTC na dniu karty (może być poza 0–1440)."""
    parsed = parse_hm(text)
    if parsed is None:
        return None
    h, m = parsed
    if h == 24 and m == 0:
        local_dt = datetime.combine(day, time.min) + timedelta(days=1)
    else:
        local_dt = datetime.combine(day, time(h, m))
    candidates = local_time_candidates(local_dt, zone)
    if not candidates:
        raise NonexistentLocalTimeError(
            f"Godzina {local_dt:%d.%m.%Y %H:%M} nie istnieje w czasie polskim."
        )
    if len(candidates) > 1 and fold is None:
        raise AmbiguousLocalTimeError(
            f"Godzina {local_dt:%d.%m.%Y %H:%M} występuje dwa razy w czasie polskim."
        )
    selected = next((item for item in candidates if fold is None or item.fold == fold), None)
    if selected is None:
        raise ValueError("Wybrany wariant czasu lokalnego nie pasuje do tej godziny.")
    base = datetime.combine(day, time.min)
    return int(round((selected.utc - base).total_seconds() / 60))


def detect_gaps(segments: list[RawSegment]) -> list[tuple[int, int]]:
    """Luki między posortowanymi blokami (prev_end, next_start). Bez wymogu pokrycia 0–1440."""
    ordered = sorted(segments, key=lambda s: (s.start_minute, s.end_minute))
    gaps: list[tuple[int, int]] = []
    for i in range(1, len(ordered)):
        prev_end = ordered[i - 1].end_minute
        next_start = ordered[i].start_minute
        if prev_end < next_start:
            gaps.append((prev_end, next_start))
    return gaps


def validate_blocks(segments: list[RawSegment]) -> str | None:
    """None = OK, inaczej komunikat błędu."""
    ordered = sorted(segments, key=lambda s: (s.start_minute, s.end_minute))
    prev_end = -1
    for s in ordered:
        if s.start_minute < 0 or s.end_minute > 1440:
            return "Godziny muszą być w zakresie 00:00–24:00 (UTC karty)."
        if s.end_minute <= s.start_minute:
            return "Koniec bloku musi być po starcie."
        if s.start_minute < prev_end:
            return "Bloki nachodzą na siebie — popraw godziny."
        prev_end = s.end_minute
    gaps = detect_gaps(ordered)
    if gaps:
        listed = ", ".join(
            f"{format_clock(a)}–{format_clock(b)}" for a, b in gaps
        )
        return (
            "Między blokami są luki czasu — uzupełnij je ręcznie "
            f"(dodaj bloki). Luki (UTC karty): {listed}."
        )
    return None
