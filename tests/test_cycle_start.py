from __future__ import annotations

from datetime import date, timedelta

from brudnopis_ot.core.calendar_weeks import ensure_monday
from brudnopis_ot.core.ddd_blocks import scenario_from_segments
from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.models import ActivityKind


def _seg(day: date, activity: str = "drive") -> RawSegment:
    return RawSegment(day=day, activity=activity, start_minute=60, end_minute=180)


def test_from_date_hides_older_blocks() -> None:
    start = date(2026, 6, 1)
    segments = [_seg(start + timedelta(days=i)) for i in range(0, 90, 7)]
    cycle = date(2026, 8, 17)
    scenario = scenario_from_segments(segments, title="t", from_date=cycle)
    assert scenario.week_start == ensure_monday(cycle)
    assert all(
        b.end > scenario.range_start
        or (
            b.kind == ActivityKind.REST
            and b.duration_hours >= 24
        )
        for b in scenario.blocks
    )
    assert min(b.start.date() for b in scenario.blocks if b.kind != ActivityKind.REST) >= ensure_monday(cycle)


def test_store_cycle_start_persists(tmp_path) -> None:
    from brudnopis_ot.core.ddd_file_loader import DddLoadResult

    store = DddProfileStore(tmp_path / "t.sqlite")
    day = date(2026, 7, 6)
    profile = DddLoadResult(
        segments=[_seg(day)],
        title="DDD: Jan",
        driver_name="Jan",
        card_number="CARD1",
        vehicle_plate=None,
        source_kind="card",
        filenames=("C_test.ddd",),
        date_from=day,
        date_to=day,
    )
    store.upsert(profile)
    monday = ensure_monday(day)
    store.set_cycle_start(profile.profile_key, monday)
    assert store.get_cycle_start(profile.profile_key) == monday
