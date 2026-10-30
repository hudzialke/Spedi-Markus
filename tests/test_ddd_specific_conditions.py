from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import struct

import pytest

from ddd_analyzer.adapter import parse_tacho_result
from ddd_analyzer.models import DddFileKind, ActivityType
from ddd_analyzer.parser import ensure_parser_path
from ddd_analyzer.specific_conditions import SpecificCondition, ferry_intervals, ferry_details
from brudnopis_ot.core.ddd_file_loader import load_ddd_file, merge_load_into_profile
from brudnopis_ot.core.ddd_import import import_files, recover_specific_conditions, refresh_profile
from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.serialize import scenario_to_dict, scenario_from_dict
from brudnopis_ot.core.validator import validate_scenario

DAY = date(2026, 10, 23)
BEGIN = datetime(2026, 10, 23, 16, 27, 25)
END = datetime(2026, 10, 23, 16, 49, 59)
FLAGS = (SpecificCondition(BEGIN, 3), SpecificCondition(END, 4))


def card_raw():
    return {'driver': {'card_number': '1234567890123456', 'surname': 'Synthetic'},
            'activities': [{'date': str(DAY), 'changes': [
                {'time': '16:19', 'activity': 'DRIVE'},
                {'time': '16:22', 'activity': 'REST'},
                {'time': '16:49', 'activity': 'DRIVE'},
                {'time': '16:50', 'activity': 'REST'}]}]}


def loaded(tmp_path, flags=FLAGS):
    path = tmp_path/'C_synthetic.ddd'
    path.write_bytes(b'synthetic-conditions')
    raw = card_raw()
    raw['specific_conditions'] = [
        {'timestamp': c.occurred_at.replace(tzinfo=timezone.utc).isoformat(), 'type_code': c.type_code}
        for c in flags]
    analysis = parse_tacho_result(raw, str(path), DddFileKind.CARD)
    return load_ddd_file(path, _analysis=analysis), path, analysis


@pytest.mark.parametrize('header', [b'', b'\0\x01'])
def test_binary_g1_g2_records_preserve_seconds_and_deduplicate(header):
    ensure_parser_path()
    from core.decoders.card_ef import parse_specific_conditions
    records = b''.join(struct.pack('>IB', int(c.occurred_at.replace(tzinfo=timezone.utc).timestamp()), c.type_code) for c in FLAGS)
    raw = card_raw()
    parse_specific_conditions(header + records + b'\0'*5, raw)
    parse_specific_conditions(records, raw)
    result = parse_tacho_result(raw, 'C_synthetic.ddd', DddFileKind.CARD)
    assert result.specific_conditions == FLAGS
    assert result.daily_summaries[0].rest_min == 27
    assert result.daily_summaries[0].drive_min == 4
    assert result.segments[1].activity == ActivityType.REST
    assert (result.segments[1].start_minute, result.segments[1].end_minute) == (982, 1009)


def test_conditions_are_independent_of_activity_and_round_trip(tmp_path):
    profile, path, _ = loaded(tmp_path)
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    assert not import_files(store, [path], loader=lambda _: [profile]).errors
    restored = DddProfileStore(store.path).get(profile.profile_key)
    assert restored == profile
    scenario = store.load_ddd_scenario(profile.profile_key)
    assert scenario.specific_conditions == FLAGS
    assert scenario_from_dict(scenario_to_dict(scenario)).specific_conditions == FLAGS
    assert '18:27:25' in ferry_details(FLAGS, BEGIN-timedelta(minutes=5), END)
    assert '16:27:25' in ferry_details(FLAGS, BEGIN-timedelta(minutes=5), END, 'UTC')
    report = validate_scenario(scenario)
    note = next(f for f in report.findings if f.code == 'FERRY_TRAIN_REVIEW')
    assert note.severity == 'info' and '60 min' in note.message_pl


def test_cross_file_flags_pair_and_do_not_delete_existing_evidence(tmp_path):
    profile, _, _ = loaded(tmp_path, FLAGS[:1])
    later = replace(profile, specific_conditions=FLAGS[1:])
    combined = merge_load_into_profile(profile, later)
    assert combined.specific_conditions == FLAGS
    assert combined.segments == profile.segments
    assert merge_load_into_profile(combined, profile).specific_conditions == FLAGS
    interval, = ferry_intervals(combined.specific_conditions)
    assert (interval.start, interval.end) == (BEGIN, END)


def test_unknown_end_does_not_mark_future_days_or_invent_a_duration():
    interval, = ferry_intervals(FLAGS[:1])
    assert interval.end is None
    assert interval.overlaps(BEGIN-timedelta(minutes=1), BEGIN+timedelta(minutes=1))
    assert not interval.overlaps(BEGIN+timedelta(days=1), BEGIN+timedelta(days=2))
    assert ferry_intervals(FLAGS[1:]) == ()


def test_midnight_out_and_duplicate_begins():
    start = datetime(2026, 10, 23, 23, 50)
    end = datetime(2026, 10, 24, 0, 20)
    records = (SpecificCondition(start, 3), SpecificCondition(start+timedelta(minutes=1), 3), SpecificCondition(end, 1))
    interval, = ferry_intervals(records)
    assert (interval.start, interval.end) == (start, end)
    assert interval.overlaps(datetime(2026, 10, 24), end)


def test_duplicate_import_adds_conditions_without_rewriting_raw_or_edits(tmp_path):
    profile, path, _ = loaded(tmp_path)
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    legacy = replace(profile, specific_conditions=())
    import_files(store, [path], loader=lambda _: [legacy])
    scenario = store.load_ddd_scenario(profile.profile_key)
    scenario.blocks[0].label = 'manual label'
    store.save_ddd_scenario(profile.profile_key, scenario)
    with store._connect() as conn:
        before = list(conn.execute('SELECT * FROM segments').fetchall())
    report = import_files(store, [path], loader=lambda _: [profile])
    assert report.skipped == 1 and not report.errors
    current = store.load_ddd_scenario(profile.profile_key)
    assert current.specific_conditions == FLAGS
    assert current.blocks[0].label == 'manual label'
    with store._connect() as conn:
        assert before == list(conn.execute('SELECT * FROM segments').fetchall())
    # Metadata enrichment does not make the older source baseline a manual edit.
    assert refresh_profile(store, profile.profile_key, loader=lambda _: [profile]).specific_conditions == FLAGS


def test_recovery_checks_hash_preserves_rows_edits_and_retries(tmp_path):
    profile, path, _ = loaded(tmp_path)
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    import_files(store, [path], loader=lambda _: [replace(profile, specific_conditions=())])
    with store._connect() as conn:
        archive = conn.execute('SELECT stored_path FROM ddd_sources').fetchone()[0]
        conn.execute('UPDATE profiles SET specific_conditions_checked=0')
        rows_before = [tuple(r) for r in conn.execute('SELECT * FROM segments')]
        baseline = conn.execute('SELECT snapshot FROM ddd_baselines').fetchone()[0]
    from pathlib import Path
    Path(archive).write_bytes(b'corrupt')
    calls = []
    def loader(p):
        calls.append(p)
        return [profile]
    assert recover_specific_conditions(store, profile.profile_key, loader=loader).specific_conditions == ()
    assert calls == []
    Path(archive).write_bytes(path.read_bytes())
    assert recover_specific_conditions(store, profile.profile_key, loader=loader).specific_conditions == FLAGS
    assert len(calls) == 1
    recover_specific_conditions(store, profile.profile_key, loader=loader)
    assert len(calls) == 1
    with store._connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM segments')] == rows_before
        assert conn.execute('SELECT snapshot FROM ddd_baselines').fetchone()[0] == baseline


def test_ui_shows_exact_flag_events_and_annotates_rest_and_drive(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from ddd_analyzer.ui.timeline_widget import TimelineWidget
    app = QApplication.instance() or QApplication([])
    _, _, analysis = loaded(tmp_path)
    widget = TimelineWidget()
    widget.set_day(DAY, analysis.segments, specific_conditions=FLAGS)
    assert widget.conditions_table.rowCount() == 2
    assert widget.conditions_table.item(0, 0).text().endswith('18:27:25')
    assert 'Prom/kolej' in widget.blocks_table.item(1, 2).text()
    assert 'Prom/kolej' in widget.blocks_table.item(2, 2).text()
    assert '18:49:59' in widget.blocks_table.item(1, 2).toolTip()
    widget.set_day(DAY+timedelta(days=1), analysis.segments)
    assert widget.conditions_table.rowCount() == 0
    widget.close()
