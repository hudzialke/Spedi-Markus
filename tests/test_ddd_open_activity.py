"""Synthetic regressions for terminal changes, supplements and old-profile recovery."""
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import json

import pytest

from ddd_analyzer.adapter import parse_tacho_result
from ddd_analyzer.models import ActivityType, DddFileKind, OpenActivity
from brudnopis_ot.core.ddd_file_loader import load_ddd_file, merge_load_into_profile
from brudnopis_ot.core.ddd_import import import_files, recover_open_activity, refresh_profile
from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.models import ActivityKind
from ewidentor.core.brudnopis_import import load_month_segments
from ewidentor.core.month_card import build_day_from_segments

DAY = date(2026, 10, 26)
START = datetime(2026, 10, 26, 9, 59)
END = datetime(2026, 10, 26, 14, 3)


def raw_card(activity='AVAILABLE'):
    return {'driver': {'card_number': '1111222233334444', 'surname': 'Synthetic'},
            'activities': [{'date': str(DAY), 'changes': [
                {'time': '00:00', 'activity': 'REST'}, {'time': '09:59', 'activity': activity}]}],
            'card_downloads': [{'download_time': '2026-10-22T09:36:37Z'}]}


def profile(tmp_path, raw=None):
    path = tmp_path / 'C_synthetic.ddd'
    path.write_bytes(b'synthetic-card-source')
    result = parse_tacho_result(raw or raw_card(), str(path), DddFileKind.CARD)
    return load_ddd_file(path, _analysis=result), path


@pytest.mark.parametrize('raw_kind,kind', [('AVAILABLE', 'availability'), ('REST', 'rest'), ('WORK', 'work'), ('DRIVE', 'drive')])
def test_terminal_change_survives_without_inventing_a_duration(tmp_path, raw_kind, kind):
    loaded, _ = profile(tmp_path, raw_card(raw_kind))
    assert loaded.open_activity == OpenActivity(ActivityType(kind), START)
    assert max(s.end_minute for s in loaded.segments) == 599
    assert sum(s.end_minute - s.start_minute for s in loaded.segments) == 599
    assert loaded.date_to == DAY


def test_midnight_only_open_card_is_importable_and_visible(tmp_path):
    raw = raw_card()
    raw['activities'][0]['changes'] = [{'time': '00:00', 'activity': 'AVAILABLE'}]
    loaded, path = profile(tmp_path, raw)
    assert loaded.segments == []
    assert loaded.open_activity.start == datetime(2026, 10, 26)
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    result = import_files(store, [path], loader=lambda _: [loaded])
    assert not result.errors and result.imported == 1
    assert store.get(loaded.profile_key).open_activity == loaded.open_activity


def test_current_download_confirms_end_and_keeps_existing_duration_contract(tmp_path):
    raw = raw_card()
    raw['card_downloads'] = [{'download_time': '2026-10-26T14:03:59Z'}]
    loaded, _ = profile(tmp_path, raw)
    assert loaded.open_activity is None
    assert loaded.segments[-1] == RawSegment(DAY, 'availability', 599, 843)


def test_confirmed_244_minutes_persist_separately_and_reach_ewidentor(tmp_path):
    loaded, path = profile(tmp_path)
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    assert not import_files(store, [path], loader=lambda _: [loaded]).errors
    raw_before = store.get(loaded.profile_key).segments
    store.confirm_activity_end(loaded.profile_key, loaded.open_activity, END)
    reopened = DddProfileStore(store.path)
    assert reopened.get(loaded.profile_key).segments == raw_before
    assert not any(b.kind == ActivityKind.AVAILABILITY for b in reopened.load_ddd_scenario(loaded.profile_key, original=True).blocks)
    edited = reopened.load_ddd_scenario(loaded.profile_key)
    available = [b for b in edited.blocks if b.kind == ActivityKind.AVAILABILITY]
    assert len(available) == 1 and (available[0].start, available[0].end) == (START, END)
    # Saving other corrections does not add a second copy of the supplement.
    edited.blocks[0].kind = ActivityKind.WORK
    reopened.save_ddd_scenario(loaded.profile_key, edited)
    assert len([b for b in reopened.load_ddd_scenario(loaded.profile_key).blocks if b.kind == ActivityKind.AVAILABILITY]) == 1
    rows = load_month_segments(store.path, loaded.profile_key, 2026, 10)
    card = build_day_from_segments(DAY, rows, night_start=time(), night_end=time(4), plan_min=0, duty_mode='raw')
    assert card.availability_min == 244
    assert card.work_min == card.duty_50_min == card.duty_100_min == 0
    # Both a repeated import and a safe source rebuild retain the supplement.
    assert import_files(store, [path], loader=lambda _: [loaded]).skipped == 1
    refresh_profile(store, loaded.profile_key, loader=lambda _: [loaded])
    assert reopened.get_confirmed_activity_end(loaded.profile_key, loaded.open_activity) == END


def test_later_source_replaces_supplement_and_resolves_old_open_change(tmp_path):
    loaded, path = profile(tmp_path)
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    import_files(store, [path], loader=lambda _: [loaded])
    store.confirm_activity_end(loaded.profile_key, loaded.open_activity, END)
    raw = raw_card()
    raw['activities'][0]['changes'].append({'time': '13:00', 'activity': 'WORK'})
    newer, _ = profile(tmp_path, raw)
    merged = merge_load_into_profile(loaded, newer)
    assert merged.open_activity == OpenActivity(ActivityType.WORK, datetime(2026, 10, 26, 13))
    store.upsert(merged)
    blocks = store.load_ddd_scenario(loaded.profile_key).blocks
    assert sum(int((b.end - b.start).total_seconds() // 60) for b in blocks if b.kind == ActivityKind.AVAILABILITY) == 181
    assert max(b.end for b in blocks) == datetime(2026, 10, 26, 13)
    # Importing the older file cannot restore a stale open activity.
    assert merge_load_into_profile(merged, loaded).open_activity == merged.open_activity


def test_new_source_with_known_end_clears_terminal_metadata(tmp_path):
    loaded, _ = profile(tmp_path)
    raw = raw_card()
    raw['card_downloads'] = [{'download_time': '2026-10-26T14:03:00Z'}]
    newer, _ = profile(tmp_path, raw)
    assert merge_load_into_profile(loaded, newer).open_activity is None


@pytest.mark.parametrize('mode', ['reimport', 'archive'])
def test_old_profile_recovery_preserves_legacy_history_edits_and_ledger(tmp_path, mode):
    loaded, path = profile(tmp_path)
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    import_files(store, [path], loader=lambda _: [replace(loaded, open_activity=None)])
    edited = store.load_ddd_scenario(loaded.profile_key)
    edited.blocks[0].kind = ActivityKind.WORK
    store.save_ddd_scenario(loaded.profile_key, edited)
    with store._connect() as conn:
        conn.execute('UPDATE profiles SET open_activity_checked=0')
        conn.execute('UPDATE ddd_baselines SET complete=0')
        # This models a pre-upgrade ledger without the new optional field.
        baseline = conn.execute('SELECT snapshot FROM ddd_baselines').fetchone()[0]
        history_before = [tuple(r) for r in conn.execute('SELECT * FROM segments')]
        edits_before = conn.execute('SELECT scenario_json FROM ddd_scenario_edits').fetchone()[0]
    if mode == 'reimport':
        assert import_files(store, [path], loader=lambda _: [loaded]).skipped == 1
    else:
        assert recover_open_activity(store, loaded.profile_key, loader=lambda _: [loaded]).open_activity == loaded.open_activity
    with store._connect() as conn:
        assert [tuple(r) for r in conn.execute('SELECT * FROM segments')] == history_before
        assert conn.execute('SELECT scenario_json FROM ddd_scenario_edits').fetchone()[0] == edits_before
        assert conn.execute('SELECT snapshot FROM ddd_baselines').fetchone()[0] == baseline
        assert conn.execute('SELECT complete FROM ddd_baselines').fetchone()[0] == 0
    assert store.get(loaded.profile_key).open_activity == loaded.open_activity
    with pytest.raises(Exception, match='bezpiecznie'):
        refresh_profile(store, loaded.profile_key)


def test_recovery_never_trusts_a_modified_archive(tmp_path):
    loaded, path = profile(tmp_path)
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    import_files(store, [path], loader=lambda _: [replace(loaded, open_activity=None)])
    with store._connect() as conn:
        conn.execute('UPDATE profiles SET open_activity_checked=0')
    store.list_archived_files(loaded.profile_key)[0].write_bytes(b'changed')
    def loader(_):
        pytest.fail('Unverified archive was parsed')
    assert recover_open_activity(store, loaded.profile_key, loader=loader).open_activity is None


def test_confirmation_rejects_stale_activity_and_invalid_end(tmp_path):
    loaded, _ = profile(tmp_path)
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    store.upsert(loaded)
    for end in [START, START - timedelta(minutes=1), END.replace(second=1)]:
        with pytest.raises(ValueError):
            store.confirm_activity_end(loaded.profile_key, loaded.open_activity, end)
    store.upsert(replace(loaded, open_activity=None))
    with pytest.raises(ValueError, match='zmieniła'):
        store.confirm_activity_end(loaded.profile_key, loaded.open_activity, END)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM ddd_activity_ends').fetchone()[0] == 0


def test_cross_month_confirmation_splits_and_stops_at_new_record(tmp_path):
    loaded, _ = profile(tmp_path)
    start = datetime(2026, 10, 31, 23)
    event = OpenActivity(ActivityType.REST, start)
    loaded = replace(loaded, open_activity=event, date_to=start.date())
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    store.upsert(loaded)
    store.confirm_activity_end(loaded.profile_key, event, datetime(2026, 11, 1, 2))
    assert [(s.start_minute, s.end_minute) for s in load_month_segments(store.path, loaded.profile_key, 2026, 11)] == [(0, 120)]
    next_event = OpenActivity(ActivityType.WORK, datetime(2026, 11, 1, 1))
    store.upsert(replace(loaded, open_activity=next_event))
    assert [(s.start_minute, s.end_minute) for s in load_month_segments(store.path, loaded.profile_key, 2026, 11)] == [(0, 60)]


def test_ui_displays_unknown_end_and_keeps_confirmation_out_of_original(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from brudnopis_ot.ui import main_window
    from brudnopis_ot.ui.activity_end_dialog import ActivityEndDialog
    app = QApplication.instance() or QApplication([])
    loaded, _ = profile(tmp_path)
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    store.upsert(loaded)
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    win = main_window.MainWindow()
    win._apply_ddd_profile(loaded)
    assert '10:59' in win._open_activity_label.text()
    assert 'Brak potwierdzonego' in win._open_activity_label.text()
    assert win._analyzer.timeline.blocks_table.item(win._analyzer.timeline.blocks_table.rowCount()-1, 1).text() == 'Nieznany koniec'
    dialog = ActivityEndDialog(loaded.open_activity, zone='Europe/Warsaw')
    dialog.accept()
    assert dialog.result() == 0 and dialog.end_utc is None
    from PySide6.QtCore import QDateTime
    dialog.end_edit.setDateTime(QDateTime(datetime(2026, 10, 26, 15, 3)))
    dialog.accept()
    assert dialog.end_utc == END
    store.confirm_activity_end(loaded.profile_key, loaded.open_activity, dialog.end_utc)
    win._apply_ddd_profile(store.get(loaded.profile_key))
    assert '4:04' in win._open_activity_label.text()
    assert any(b.start == START and b.end == END for b in win._scenario.blocks)
    win._ddd_view.setCurrentIndex(1)
    assert not win._activity_end_button.isEnabled()
    assert not any(b.kind == ActivityKind.AVAILABILITY for b in win._scenario.blocks)
    win.close(); win.deleteLater(); dialog.deleteLater(); app.processEvents()
