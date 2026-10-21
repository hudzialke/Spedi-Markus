from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Sequence

from brudnopis_ot.core.calendar_weeks import ensure_monday
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.models import ActivityKind, Block, Scenario

if TYPE_CHECKING:
    from brudnopis_ot.core.ddd_file_loader import BorderCrossingRecord, PlaceRecord

_MAX_WEEKS = 12

_ACTIVITY_MAP: dict[str, ActivityKind] = {
    "drive": ActivityKind.DRIVE,
    "DRIVE": ActivityKind.DRIVE,
    "work": ActivityKind.WORK,
    "WORK": ActivityKind.WORK,
    "availability": ActivityKind.AVAILABILITY,
    "AVAILABILITY": ActivityKind.AVAILABILITY,
    "AVAILABLE": ActivityKind.AVAILABILITY,
    "available": ActivityKind.AVAILABILITY,
    "rest": ActivityKind.REST,
    "REST": ActivityKind.REST,
}


def map_activity(activity: str) -> ActivityKind | None:
    key = (activity or "").strip()
    if key in _ACTIVITY_MAP:
        return _ACTIVITY_MAP[key]
    lower = key.lower()
    return _ACTIVITY_MAP.get(lower)


def segment_to_datetimes(seg: RawSegment) -> tuple[datetime, datetime] | None:
    """Convert day + minute range to absolute datetimes. end_minute 1440 = next midnight."""
    if seg.end_minute <= seg.start_minute:
        return None
    if seg.start_minute < 0 or seg.end_minute > 1440:
        return None
    start = datetime.combine(seg.day, datetime.min.time()) + timedelta(minutes=seg.start_minute)
    if seg.end_minute == 1440:
        end = datetime.combine(seg.day + timedelta(days=1), datetime.min.time())
    else:
        end = datetime.combine(seg.day, datetime.min.time()) + timedelta(minutes=seg.end_minute)
    if end <= start:
        return None
    return start, end


def segments_to_blocks(segments: list[RawSegment]) -> list[Block]:
    blocks: list[Block] = []
    for seg in segments:
        kind = map_activity(seg.activity)
        if kind is None:
            continue
        bounds = segment_to_datetimes(seg)
        if bounds is None:
            continue
        start, end = bounds
        blocks.append(Block(kind=kind, start=start, end=end))
    return merge_contiguous_blocks(blocks)


def _country_code(value: str | None) -> str | None:
    code = (value or "").strip().upper()
    return code if len(code) == 2 and code.isalpha() else None


def assign_countries_to_blocks(
    blocks: list[Block],
    *,
    employer_country: str,
    places: Sequence[PlaceRecord] = (),
    border_crossings: Sequence[BorderCrossingRecord] = (),
) -> list[Block]:
    """Assign the last known DDD country at the start of every activity block."""
    events: list[tuple[datetime, str]] = []
    for item in places:
        code = _country_code(item.country_code)
        if code is not None:
            events.append((item.occurred_at, code))
    for item in border_crossings:
        code = _country_code(item.country_entered)
        if code is not None:
            events.append((item.occurred_at, code))
    events.sort(key=lambda item: item[0])
    if not events:
        return blocks

    instants = [instant for instant, _ in events]
    base_country = _country_code(employer_country)
    annotated: list[Block] = []
    for block in blocks:
        index = bisect_right(instants, block.start) - 1
        country = events[index][1] if index >= 0 else None
        annotated.append(
            Block(
                id=block.id,
                kind=block.kind,
                start=block.start,
                end=block.end,
                country_start=country,
                started_abroad=bool(
                    country is not None
                    and base_country is not None
                    and country != base_country
                ),
                label=block.label,
            )
        )
    return annotated


def merge_contiguous_blocks(blocks: list[Block]) -> list[Block]:
    """
    Merge adjacent/overlapping blocks of the same activity kind.
    Fixes midnight splits from per-day DDD segments (e.g. 22:00–24:00 + 00:00–08:00 → 10h).
    """
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: (b.start, b.end, b.id))
    merged: list[Block] = []
    cur = ordered[0]
    for nxt in ordered[1:]:
        if nxt.kind == cur.kind and nxt.start <= cur.end:
            cur = Block(
                id=cur.id,
                kind=cur.kind,
                start=cur.start,
                end=max(cur.end, nxt.end),
                country_start=cur.country_start or nxt.country_start,
                started_abroad=cur.started_abroad or nxt.started_abroad,
                label=cur.label or nxt.label,
            )
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)
    return merged


def scenario_from_segments(
    segments: list[RawSegment],
    *,
    title: str,
    employer_country: str = "PL",
    from_date: date | None = None,
    places: Sequence[PlaceRecord] = (),
    border_crossings: Sequence[BorderCrossingRecord] = (),
) -> Scenario:
    blocks = segments_to_blocks(segments)
    blocks = assign_countries_to_blocks(
        blocks,
        employer_country=employer_country,
        places=places,
        border_crossings=border_crossings,
    )
    if not blocks and not segments:
        monday = ensure_monday(from_date or date.today())
        return Scenario(
            week_start=monday,
            weeks=1,
            employer_country=employer_country,
            title=title,
            blocks=[],
        )

    if blocks:
        first_day = min(b.start.date() for b in blocks)
        last_day = max((b.end - timedelta(seconds=1)).date() for b in blocks)
    else:
        first_day = min(s.day for s in segments)
        last_day = max(s.day for s in segments)

    if from_date is not None:
        week_start = ensure_monday(from_date)
        last_monday = ensure_monday(last_day)
        weeks = max(1, ((last_monday - week_start).days // 7) + 1)
        if weeks < 1:
            weeks = 1
        scenario = Scenario(
            week_start=week_start,
            weeks=min(weeks, _MAX_WEEKS),
            employer_country=employer_country,
            title=title,
            blocks=blocks,
        )
        scenario.set_axis_monday(week_start)
        return scenario

    week_start = ensure_monday(first_day)
    last_monday = ensure_monday(last_day)
    weeks = max(1, ((last_monday - week_start).days // 7) + 1)
    if weeks > _MAX_WEEKS:
        # Prefer most recent window for long tachograph histories
        week_start = last_monday - timedelta(weeks=_MAX_WEEKS - 1)
        weeks = _MAX_WEEKS

    return Scenario(
        week_start=week_start,
        weeks=weeks,
        employer_country=employer_country,
        title=title,
        blocks=blocks,
    )
