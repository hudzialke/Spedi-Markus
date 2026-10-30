"""Tachograph condition flags are evidence alongside the four activities.

Times remain naive UTC, including seconds. A ferry flag never changes REST,
DRIVE or their totals and does not prove accommodation required by Article 9.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta


CONDITION_LABELS = {
    1: "Poza zakresem — początek", 2: "Poza zakresem — koniec",
    3: "Prom/kolej — początek", 4: "Prom/kolej — koniec",
}
FERRY_COLOR = "#146c94"
FERRY_REVIEW = (
    "Prom/kolej zapisany w DDD. Aktywności i czas jazdy pozostają rozliczane osobno. "
    "Przerywany odpoczynek wymaga oceny art. 9: maksymalnie dwie przerwy, "
    "łącznie do 60 min, wymagany wymiar odpoczynku i dostęp do miejsca do spania; "
    "dla regularnego tygodniowego także rejs planowany na co najmniej 8 h i kabina. "
    "Sama flaga DDD nie potwierdza tych warunków."
)


@dataclass(frozen=True, slots=True, order=True)
class SpecificCondition:
    occurred_at: datetime
    type_code: int

    @property
    def label(self):
        return CONDITION_LABELS.get(self.type_code, f"Warunek {self.type_code}")


@dataclass(frozen=True, slots=True)
class FerryInterval:
    start: datetime
    end: datetime | None

    def overlaps(self, start, end):
        if self.end is None:
            return start <= self.start < end
        return self.start < end and self.end > start


def merge_conditions(*collections):
    return tuple(sorted({item for items in collections for item in items}))


def ferry_intervals(conditions):
    """Pair flags across files/midnights; never invent an end for a lone begin.

    Repeated begins do not erase the first evidence. OUT begin ends the ferry
    condition per Annex IC 3.6.3. Orphan end flags remain available as events.
    """
    start = None
    result = []
    for item in merge_conditions(conditions):
        if item.type_code == 3 and start is None:
            start = item.occurred_at
        elif item.type_code in (1, 4) and start is not None:
            if item.occurred_at > start:
                result.append(FerryInterval(start, item.occurred_at))
            start = None
    if start is not None:
        result.append(FerryInterval(start, None))
    return tuple(result)


def ferry_details(conditions, start, end, zone="Europe/Warsaw"):
    from ddd_analyzer.display_tz import format_dt
    details = []
    for interval in ferry_intervals(conditions):
        if interval.overlaps(start, end):
            last = format_dt(interval.end, zone, '%d.%m %H:%M:%S') if interval.end else 'nieznany koniec'
            details.append(f"Prom/kolej (DDD): {format_dt(interval.start, zone, '%d.%m %H:%M:%S')} — {last}")
    return "\n".join(details)


def segment_bounds(segment):
    midnight = datetime.combine(segment.day, datetime.min.time())
    return (midnight + timedelta(minutes=segment.start_minute),
            midnight + timedelta(minutes=segment.end_minute))
