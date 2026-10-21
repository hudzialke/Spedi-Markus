"""Display timezone for tachograph UTC times (card = UTC)."""

from __future__ import annotations

from spedi.i18n import tr as _tr

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

DisplayZone = Literal["Europe/Warsaw", "UTC"]

DEFAULT_ZONE: DisplayZone = "Europe/Warsaw"
SETTINGS_KEY = "display_zone"
SETTINGS_ORG = "Tachomajster"
SETTINGS_APP = "DisplayTimezone"

ZONE_LABELS: dict[DisplayZone, str] = {
    "Europe/Warsaw": _tr("Czas PL (Europe/Warsaw)"),
    "UTC": _tr("UTC karty"),
}


class NonexistentLocalTimeError(ValueError):
    """The entered wall-clock time is skipped by a DST transition."""


class AmbiguousLocalTimeError(ValueError):
    """The entered wall-clock time occurs twice during a DST transition."""


@dataclass(frozen=True, slots=True)
class LocalTimeCandidate:
    utc: datetime
    fold: int
    utc_offset: timedelta

    @property
    def offset_label(self) -> str:
        minutes = int(self.utc_offset.total_seconds() // 60)
        sign = "+" if minutes >= 0 else "-"
        hours, remainder = divmod(abs(minutes), 60)
        return f"UTC{sign}{hours:02d}:{remainder:02d}"


def normalize_zone(value: str | None) -> DisplayZone:
    if value == "UTC":
        return "UTC"
    return "Europe/Warsaw"


def zoneinfo_for(zone: DisplayZone) -> ZoneInfo | timezone:
    if zone == "UTC":
        return timezone.utc
    return ZoneInfo("Europe/Warsaw")


def utc_naive_to_zone(dt: datetime, zone: DisplayZone = DEFAULT_ZONE) -> datetime:
    """Naive UTC wall clock → naive wall clock in *zone* (tzinfo stripped)."""
    if dt.tzinfo is not None:
        aware_utc = dt.astimezone(timezone.utc)
    else:
        aware_utc = dt.replace(tzinfo=timezone.utc)
    local = aware_utc.astimezone(zoneinfo_for(zone))
    return local.replace(tzinfo=None)


def local_time_candidates(
    dt: datetime, zone: DisplayZone = DEFAULT_ZONE
) -> tuple[LocalTimeCandidate, ...]:
    """Return valid UTC interpretations of a local wall-clock value."""
    if dt.tzinfo is not None:
        offset = dt.utcoffset() or timedelta(0)
        return (LocalTimeCandidate(dt.astimezone(timezone.utc).replace(tzinfo=None), dt.fold, offset),)
    if zone == "UTC":
        return (LocalTimeCandidate(dt, 0, timedelta(0)),)
    local_zone = ZoneInfo("Europe/Warsaw")
    found: list[LocalTimeCandidate] = []
    for fold in (0, 1):
        aware = dt.replace(tzinfo=local_zone, fold=fold)
        utc = aware.astimezone(timezone.utc)
        roundtrip = utc.astimezone(local_zone)
        if roundtrip.replace(tzinfo=None) != dt or roundtrip.fold != fold:
            continue
        candidate = LocalTimeCandidate(
            utc=utc.replace(tzinfo=None),
            fold=fold,
            utc_offset=aware.utcoffset() or timedelta(0),
        )
        if all(item.utc != candidate.utc for item in found):
            found.append(candidate)
    return tuple(sorted(found, key=lambda item: item.utc))


def zone_naive_to_utc(
    dt: datetime, zone: DisplayZone = DEFAULT_ZONE, *, fold: int | None = None
) -> datetime:
    """Local wall clock → UTC; reject DST gaps and unresolved repetitions."""
    if zone == "UTC":
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.replace(tzinfo=None)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    candidates = local_time_candidates(dt, zone)
    if not candidates:
        raise NonexistentLocalTimeError(
            f"Godzina {dt:%d.%m.%Y %H:%M} nie istnieje w strefie Europe/Warsaw "
            "z powodu przejścia na czas letni."
        )
    if len(candidates) > 1 and fold is None:
        raise AmbiguousLocalTimeError(
            f"Godzina {dt:%d.%m.%Y %H:%M} występuje dwa razy podczas przejścia na czas zimowy."
        )
    if fold is not None:
        selected = next((item for item in candidates if item.fold == fold), None)
        if selected is None:
            raise ValueError("Wybrany wariant czasu lokalnego nie pasuje do tej godziny.")
        return selected.utc
    return candidates[0].utc


def utc_minutes_to_zone(
    day: date,
    start_minute: int,
    end_minute: int,
    zone: DisplayZone = DEFAULT_ZONE,
) -> tuple[datetime, datetime]:
    """Card day + minutes (UTC) → start/end naive datetimes in *zone*."""
    start_utc = datetime.combine(day, time.min) + timedelta(minutes=start_minute)
    end_utc = datetime.combine(day, time.min) + timedelta(minutes=end_minute)
    return utc_naive_to_zone(start_utc, zone), utc_naive_to_zone(end_utc, zone)


def format_dt(dt: datetime, zone: DisplayZone = DEFAULT_ZONE, fmt: str = "%d.%m.%Y %H:%M") -> str:
    return utc_naive_to_zone(dt, zone).strftime(fmt)


def zone_label(zone: DisplayZone) -> str:
    return ZONE_LABELS[zone]
