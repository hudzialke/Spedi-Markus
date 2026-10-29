from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
import inspect

import pytest

from brudnopis_ot.core import weekly_rules
from brudnopis_ot.core.ddd_file_loader import DddLoadResult
from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.models import ActivityKind, Block, Scenario
from brudnopis_ot.core.serialize import scenario_to_dict
from spedi import mobile_rules
from spedi.mobile_schedule import build_driver_documents, build_driver_schedule, driver_document_id


def pair_scenario():
    return Scenario(week_start=date(2026, 9, 21), blocks=[
        Block(ActivityKind.DRIVE, datetime(2026, 9, 21, 5), datetime(2026, 9, 21, 6), id="anchor"),
        Block(ActivityKind.REST, datetime(2026, 9, 25, 18), datetime(2026, 9, 26, 18), id="r1", started_abroad=True),
        Block(ActivityKind.REST, datetime(2026, 10, 2, 18), datetime(2026, 10, 3, 18), id="r2", started_abroad=True),
    ], anchor_block_id="anchor")


def schedule(scenario, **kwargs):
    return build_driver_schedule(profile_key="card:ABC", display_name="Jan", scenario=scenario,
                                 cycle_known=True, **kwargs)


def test_pair_deadline_includes_compensations_before_regular_rest():
    doc = schedule(pair_scenario())
    assert doc["rest_start_by"] == "2026-10-08T00:00:00Z"
    assert doc["next_weekly_rest_start_by"] == "2026-10-09T18:00:00Z"
    assert doc["pending_compensations"] == [{
        "source_block_ids": ["r1", "r2"], "remaining_minutes": 42 * 60,
        "compensation_due_at": "2026-10-09T18:00:00Z",
        "latest_compensation_start_at": "2026-10-08T00:00:00Z", "mobility_pair": True,
    }]


def test_planned_return_never_changes_analyzer_deadline():
    first = schedule(pair_scenario(), planned_return_at="2026-10-07T12:00:00+02:00")
    second = schedule(pair_scenario(), planned_return_at="2026-10-10T12:00:00+02:00")
    assert first["planned_return_at"] == "2026-10-07T10:00:00Z"
    assert first["rest_start_by"] == second["rest_start_by"]
    assert first["pending_compensations"] == second["pending_compensations"]
    with pytest.raises(ValueError, match="strefy"):
        schedule(pair_scenario(), planned_return_at="2026-10-07T12:00")


def test_paid_pair_does_not_leave_a_false_compensation_reminder():
    sc = pair_scenario()
    start = datetime(2026, 10, 7)
    sc.blocks.append(Block(ActivityKind.REST, start, start + timedelta(hours=87), id="paid"))
    doc = schedule(sc)
    assert doc["pending_compensations"] == []
    assert doc["rest_start_by"] == doc["next_weekly_rest_start_by"]


def test_single_reduced_rest_keeps_engine_due_date_without_inventing_pm_start():
    sc = pair_scenario()
    sc.blocks.pop()
    debt = schedule(sc)["pending_compensations"][0]
    assert debt["remaining_minutes"] == 21 * 60
    assert debt["compensation_due_at"] == "2026-10-19T00:00:00Z"
    assert debt["latest_compensation_start_at"] is None
    assert debt["mobility_pair"] is False


@pytest.mark.parametrize("monday,utc", [
    (date(2026, 3, 9), "2026-03-29T22:00:00Z"),
    (date(2026, 10, 5), "2026-10-25T23:00:00Z"),
])
def test_week4_is_calendar_monday_in_warsaw_across_dst(monday, utc):
    doc = schedule(Scenario(week_start=monday))
    assert doc["week4_start_at"] == utc
    assert doc["week4_monday"] == (monday + timedelta(days=21)).isoformat()


def test_all_profiles_modified_view_and_stable_identity(tmp_path):
    store = DddProfileStore(tmp_path / "profiles.sqlite")
    day = date(2026, 9, 21)
    p = DddLoadResult([RawSegment(day, "drive", 60, 180)], "Jan", "Jan", "ABC", None,
                      "card", ("source.ddd",), day, day)
    store.upsert(p)
    changed = store.load_ddd_scenario(p.profile_key)
    changed.blocks[0].kind = ActivityKind.WORK
    changed.anchor_block_id = changed.blocks[0].id
    store.save_ddd_scenario(p.profile_key, changed)
    manual = store.create_template("Jan", day)  # Identical name, independent identity.
    unknown = DddLoadResult([RawSegment(day, "drive", 60, 120)], "Bez startu", "Bez startu",
                            "DEF", None, "card", ("second.ddd",), day, day)
    store.upsert(unknown)
    docs = build_driver_documents(store)
    assert len(docs) == 3
    edited = docs[driver_document_id(p.profile_key)]
    assert edited["scenario"]["blocks"][0]["kind"] == "work"
    assert store.load_ddd_scenario(p.profile_key, original=True).blocks[0].kind == ActivityKind.DRIVE
    assert driver_document_id(p.profile_key) != driver_document_id(manual.profile_key)
    missing = docs[driver_document_id(unknown.profile_key)]
    assert missing["cycle_known"] is False
    assert missing["week4_start_at"] is None
    assert missing["anchor_at"] is None


def test_export_does_not_modify_scenario_and_revision_is_stable():
    sc = pair_scenario()
    original = scenario_to_dict(deepcopy(sc))
    assert schedule(sc)["revision"] == schedule(sc)["revision"]
    assert scenario_to_dict(sc) == original
    assert schedule(sc)["revision"] != schedule(sc, planned_return_at="2026-10-08T12:00:00Z")["revision"]


def test_export_profile_list_uses_metadata_instead_of_loading_all_raw_history(tmp_path, monkeypatch):
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    profile = store.create_template('Synthetic', date(2026, 10, 5))
    def forbidden():
        pytest.fail('Export should load each scenario once, not materialize full profile lists')
    monkeypatch.setattr(store, 'list_profiles', forbidden)
    documents = build_driver_documents(store)
    assert documents[driver_document_id(profile.profile_key)]['display_name'] == 'Synthetic'


def test_lightweight_phone_publication_keeps_full_revision_without_retaining_timelines(tmp_path):
    from spedi.mobile_sync import published_document
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    profile = store.create_template('Synthetic', date(2026, 9, 21))
    store.save_template_scenario(profile.profile_key, pair_scenario())
    full = build_driver_documents(store)
    lightweight = build_driver_documents(store, include_scenario=False)
    key = driver_document_id(profile.profile_key)
    assert 'scenario' in full[key] and 'scenario' not in lightweight[key]
    assert lightweight[key]['revision'] == full[key]['revision']
    assert published_document(lightweight[key]) == published_document(full[key])


def test_every_consumer_uses_the_same_authoritative_rule_functions():
    from brudnopis_ot.core import validator
    from spedi import weekly_watch, mobile_schedule
    assert mobile_rules.analyze_scenario is weekly_rules.analyze_scenario
    assert weekly_watch.analyze_scenario is weekly_rules.analyze_scenario
    assert mobile_schedule.analyze_scenario is weekly_rules.analyze_scenario
    assert validator.analyze_scenario is weekly_rules.analyze_scenario
    assert mobile_rules._Debt is weekly_rules._Debt
    for sc in [pair_scenario(), Scenario(week_start=date(2026, 9, 21))]:
        assert mobile_rules.analyze_scenario(sc) == weekly_rules.analyze_scenario(sc)


def test_phone_and_watchlist_share_the_home_return_plan():
    from spedi.weekly_watch import calculate, WatchOptions, history_revision
    from tests.test_compensation_assignment import crossing_history
    for sc in (pair_scenario(), crossing_history()):
        options = WatchOptions(verified_revision=history_revision(sc))
        result = calculate(sc, options)
        phone = schedule(sc, watch_options=options)
        from spedi.mobile_schedule import utc_iso
        assert phone['home_return_by'] == utc_iso(result.return_by)
        assert phone['home_return_reason'] == result.return_reason
        assert phone['home_return_rest_minutes'] == result.return_rest_minutes


def test_missing_cycle_never_exports_a_guessed_home_return():
    document = build_driver_schedule(profile_key='demo', display_name='Synthetic',
                                    scenario=pair_scenario(), cycle_known=False)
    assert document['home_return_by'] is None
