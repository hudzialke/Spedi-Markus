"""Anonymous UTC fixtures for fixed-week compensation and arrival planning."""
from datetime import date, datetime, timedelta

from brudnopis_ot.core.models import ActivityKind, Block, Scenario
from spedi.mobile_rules import build_weekly_rests
from spedi.weekly_watch import (
    WatchOptions, _compensation_assignments, calculate, history_revision, midnight,
)


def crossing_history():
    return Scenario(week_start=date(2026, 10, 5), anchor_block_id='departure', blocks=[
        Block(ActivityKind.REST, datetime(2026, 9, 7), datetime(2026, 10, 5, 11, 38), id='home'),
        Block(ActivityKind.WORK, datetime(2026, 10, 5, 11, 38), datetime(2026, 10, 5, 11, 39), id='departure'),
        Block(ActivityKind.REST, datetime(2026, 10, 11, 11, 29), datetime(2026, 10, 12, 12, 53), id='crossing'),
        Block(ActivityKind.REST, datetime(2026, 10, 16, 15, 38), datetime(2026, 10, 19, 5, 29), id='regular'),
        Block(ActivityKind.REST, datetime(2026, 10, 24, 15, 24), datetime(2026, 10, 25, 15, 33), id='last'),
    ])


def test_crossing_reduction_does_not_force_early_home_arrival():
    scenario = crossing_history()
    result = calculate(scenario, WatchOptions(verified_revision=history_revision(scenario)))
    assert result.weekly_start_by == datetime(2026, 10, 31, 15, 33)
    assert result.return_by == result.weekly_start_by
    assert result.return_compensation_minutes == 0  # Due after this return cycle.
    assert 'od 12.10.2026' in result.return_reason
    assert '09.11.2026 00:00' in result.return_reason
    assert result.return_by + timedelta(hours=45, minutes=19*60+36) < midnight(date(2026, 11, 9))


def test_week_shift_must_preserve_every_two_week_window():
    scenario = Scenario(week_start=date(2026, 10, 5), blocks=[
        Block(ActivityKind.REST, datetime(2026, 10, 10, 15), datetime(2026, 10, 12, 12), id='week1'),
        Block(ActivityKind.REST, datetime(2026, 10, 18, 12), datetime(2026, 10, 19, 14), id='crossing'),
        Block(ActivityKind.REST, datetime(2026, 10, 24, 9), datetime(2026, 10, 26, 6), id='week3'),
    ])
    rests = [r for r in build_weekly_rests(scenario) if r.is_weekly]
    assignments = list(_compensation_assignments(rests, scenario.week_start, datetime(2026, 10, 31)))
    assert assignments and all(a[('crossing',)] == date(2026, 10, 12) for a in assignments)


def test_exact_monday_end_does_not_create_a_new_assignment_week():
    scenario = crossing_history()
    block = scenario.get_block('crossing')
    # Monday 00:00 Warsaw is Sunday 22:00 UTC before the DST change.
    block.start = datetime(2026, 10, 10, 20)
    block.end = datetime(2026, 10, 11, 22)
    rests = [r for r in build_weekly_rests(scenario) if r.is_weekly]
    assignments = list(_compensation_assignments(rests, scenario.week_start, datetime(2026, 10, 31)))
    assert all(a.get(('crossing',), date(2026, 10, 5)) == date(2026, 10, 5) for a in assignments)


def test_ambiguous_but_infeasible_history_keeps_conservative_dates():
    scenario = Scenario(week_start=date(2026, 10, 5), blocks=[
        Block(ActivityKind.REST, datetime(2026, 10, 11, 12), datetime(2026, 10, 12, 12), id='only'),
    ])
    rests = build_weekly_rests(scenario)
    assert list(_compensation_assignments(rests, scenario.week_start, datetime(2026, 10, 31))) == [{}]


def test_unambiguous_debts_do_not_search_assignments(monkeypatch):
    from spedi import weekly_watch
    scenario = Scenario(week_start=date(2026, 10, 5), blocks=[
        Block(ActivityKind.REST, datetime(2026, 10, 9), datetime(2026, 10, 10), id='single'),
    ])
    def forbidden(*args):
        raise AssertionError('No crossing debt requires an assignment search')
    monkeypatch.setattr(weekly_watch, '_compensation_assignments', forbidden)
    calculate(scenario, WatchOptions(verified_revision=history_revision(scenario)))


def test_assignment_cannot_ignore_an_exceeded_six_day_gap():
    scenario = crossing_history()
    scenario.get_block('crossing').start += timedelta(minutes=10)
    # The first gap is now 144h01m, despite adequate calendar-week totals.
    rests = [r for r in build_weekly_rests(scenario) if r.is_weekly]
    assert list(_compensation_assignments(rests, scenario.week_start, datetime(2026, 10, 31))) == [{}]


def test_splitting_a_regular_rest_cannot_invent_an_unpaid_reduction():
    scenario = Scenario(week_start=date(2026, 10, 5), blocks=[
        Block(ActivityKind.REST, datetime(2026, 10, 11), datetime(2026, 10, 13, 21), id='69h'),
        Block(ActivityKind.REST, datetime(2026, 10, 19, 20), datetime(2026, 10, 20, 20), id='reduced'),
    ])
    # Crediting the first 24h to week1 and the remaining45h to week2 would
    # need a further21h compensation. It cannot silently extend arrival.
    rests = build_weekly_rests(scenario)
    assert list(_compensation_assignments(rests, scenario.week_start, datetime(2026, 10, 26, 20))) == [{}]
