from dataclasses import replace
from datetime import date, datetime, time, timezone
from pathlib import Path
import json
import struct

import pytest

from ddd_analyzer.parser import ensure_parser_path
ensure_parser_path()
from core.decoders.common import decode_activity_val, parse_cyclic_buffer_activities
from core.decoders.vu_g1 import _parse_trep_02_g1_structured
from ddd_analyzer.adapter import parse_tacho_result
from ddd_analyzer.models import ActivityType, DddFileKind
from brudnopis_ot.core.ddd_blocks import scenario_from_segments
from brudnopis_ot.core.ddd_file_loader import DddLoadResult, DddLoadError, load_ddd_profiles
from brudnopis_ot.core.ddd_import import import_files, refresh_profile
from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.models import ActivityKind, ACTIVITY_COLORS
from ewidentor.core.brudnopis_import import load_month_segments
from ewidentor.core.month_card import build_day_from_segments

DAY = date(2026, 7, 6)
CARD = '1234567890123456'
OTHER = '2234567890123456'


def aci(activity, minute, slot=0, inserted=True):
    return (slot << 15) | (int(not inserted) << 13) | (activity << 11) | minute


def card_raw():
    values = [aci(0, 0), aci(1, 60, 1), aci(2, 90, 1), aci(3, 120), aci(0, 180)]
    ts = int(datetime.combine(DAY, time(), timezone.utc).timestamp())
    data = b'\0'*4 + struct.pack('>HHIHH', 0, 12+len(values)*2, ts, 1, 0) + struct.pack('>'+'H'*len(values), *values)
    raw = {'activities': [], 'driver': {'card_number': CARD, 'surname': 'Test'}, 'metadata': {'is_vu': False}}
    parse_cyclic_buffer_activities(data, raw)
    return raw


def session(number=CARD, slot=0, start='01:00', end='03:00'):
    return {'card': {'card_type': 1, 'card_number': number, 'nation': 'PL'},
            'card_slot': slot, 'insertion_time': f'{DAY}T{start}:00+00:00',
            'withdrawal_time': f'{DAY}T{end}:00+00:00', 'holder_surname': 'Test'}


def vu_raw():
    values = [aci(0, 0, inserted=False), aci(1, 60), aci(2, 90), aci(3, 120), aci(0, 180, inserted=False),
              aci(1, 0, 1, False), aci(1, 60, 1), aci(0, 180, 1, False)]
    ts = int(datetime.combine(DAY, time(), timezone.utc).timestamp())
    data = struct.pack('>I', ts) + b'\0'*3 + struct.pack('>HH', 0, len(values)) + struct.pack('>'+'H'*len(values), *values) + b'\0'*3
    raw = {'metadata': {'is_vu': True}, 'vehicle': {'plate': 'WX123'}}
    assert _parse_trep_02_g1_structured(data, raw)
    raw['card_iw_records'] = [session(), session(OTHER, 1)]
    return raw


def profile(number=CARD, activity='availability', start=60, end=90):
    return DddLoadResult([RawSegment(DAY, activity, start, end)], 'Test', 'Test', number, None,
                         'card', ('test.ddd',), DAY, DAY)


@pytest.mark.parametrize('code,expected', [(0,'REST'), (1,'AVAILABLE'), (2,'WORK'), (3,'DRIVE')])
def test_raw_bits(code, expected):
    assert decode_activity_val(aci(code, 60))['activity'] == expected


def test_card_binary_available_survives_second_slot_and_summarizes():
    analysis = parse_tacho_result(card_raw(), 'C.ddd', DddFileKind.CARD)
    assert [s.activity for s in analysis.segments] == [ActivityType.REST, ActivityType.AVAILABILITY, ActivityType.WORK, ActivityType.DRIVE]
    day = analysis.daily_summaries[0]
    # The final REST change has no recorded end: no invented rest until midnight.
    assert (day.availability_min, day.work_min, day.drive_min, day.rest_min) == (30, 30, 60, 60)


def test_vu_available_attributed_to_correct_slot_driver():
    a = parse_tacho_result(vu_raw(), 'M.ddd', DddFileKind.VU)
    by_card = {p.driver.card_number: p for p in a.driver_analyses}
    assert set(by_card) == {CARD, OTHER}
    assert by_card[CARD].daily_summaries[0].availability_min == 30
    assert by_card[OTHER].daily_summaries[0].availability_min == 120
    assert by_card[CARD].daily_summaries[0].drive_min == 60


@pytest.mark.parametrize('case', ['no_iw', 'missing_withdrawal', 'empty', 'unknown_slot', 'overlap'])
def test_vu_ambiguous_or_empty_not_assigned(case):
    raw = vu_raw()
    raw['card_iw_records'] = [session()]
    if case == 'no_iw': raw['card_iw_records'] = []
    elif case == 'missing_withdrawal': raw['card_iw_records'][0]['withdrawal_time'] = None
    elif case == 'unknown_slot': raw['card_iw_records'][0]['card_slot'] = 2
    elif case == 'overlap': raw['card_iw_records'].append(session(OTHER))
    else:
        for c in raw['activities'][0]['changes']: c['card_inserted'] = False
    assert parse_tacho_result(raw, 'M.ddd', DddFileKind.VU).driver_analyses == []


def test_vu_only_second_slot_not_fallback_vehicle_driver():
    raw = vu_raw()
    raw['activities'][0]['changes'] = [c for c in raw['activities'][0]['changes'] if c['slot'] == 'Second']
    a = parse_tacho_result(raw, 'M.ddd', DddFileKind.VU)
    assert a.segments == []
    assert {p.driver.card_number for p in a.driver_analyses} == {OTHER}


def test_scenario_and_legend_separate_availability():
    p = profile()
    scenario = scenario_from_segments(p.segments, title=p.title)
    assert scenario.blocks[0].kind == ActivityKind.AVAILABILITY
    assert ACTIVITY_COLORS[ActivityKind.AVAILABILITY] not in [ACTIVITY_COLORS[k] for k in (ActivityKind.REST, ActivityKind.WORK, ActivityKind.DRIVE)]


def test_sqlite_to_ewidentor_raw_no_duty_or_work(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    store.upsert(profile())
    segments = load_month_segments(store.path, profile().profile_key, 2026, 7)
    card = build_day_from_segments(DAY, segments, night_start=time(0), night_end=time(4), plan_min=0, duty_mode='raw', night_on_availability=True)
    assert card.availability_min == 30
    assert (card.work_min, card.other_work_min, card.rest_min, card.duty_50_min, card.duty_100_min, card.break_15_min, card.night_min) == (0,)*7


def files(tmp_path, payloads):
    paths = []
    for i, value in enumerate(payloads):
        p = tmp_path/f'{i}.ddd'
        p.write_bytes(value)
        paths.append(p)
    return paths


def test_batch_same_driver_overlap_union_and_archive_dedupe(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    paths = files(tmp_path, [b'one', b'two'])
    def loader(p): return [profile(start=60 if p.read_bytes()==b'one' else 75, end=90 if p.read_bytes()==b'one' else 120)]
    report = import_files(store, paths, loader=loader)
    assert (report.imported, report.merged, report.skipped, report.errors) == (1,1,0,[])
    assert len(store.list_profiles()) == 1
    assert store.get(profile().profile_key).segments == [RawSegment(DAY,'availability',60,120)]
    again = import_files(store, paths, loader=loader)
    renamed = tmp_path/'renamed.ddd'; renamed.write_bytes(b'one')
    third = import_files(store, [renamed], loader=loader)
    assert again.skipped == 2 and third.skipped == 1
    assert len(store.list_archived_files(profile().profile_key)) == 2
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM ddd_sources').fetchone()[0] == 2


def test_batch_different_drivers_bad_file_isolated_template_preserved(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    template = store.create_template('Test', DAY)
    paths = files(tmp_path, [b'one', b'bad', b'two'])
    def loader(p):
        if p.read_bytes() == b'bad': raise ValueError('Uszkodzony plik')
        return [profile(CARD if p.read_bytes()==b'one' else OTHER)]
    report = import_files(store, paths, loader=loader)
    assert report.imported == 2 and len(report.errors) == 1
    assert '1.ddd: Uszkodzony plik' in report.errors[0]
    assert len(store.list_profiles()) == 3
    assert store.get(template.profile_key) == template


def test_refresh_same_sources_only_other_profile_and_cycle_unchanged(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    paths = files(tmp_path, [b'one'])
    import_files(store, paths, loader=lambda p: [profile()])
    store.upsert(profile(OTHER))
    store.set_cycle_start(profile().profile_key, DAY)
    refreshed = refresh_profile(store, profile().profile_key, loader=lambda p: [profile(activity='work')])
    assert refreshed.segments[0].activity == 'work'
    assert store.get(profile(OTHER).profile_key).segments[0].activity == 'availability'
    assert store.get_cycle_start(profile().profile_key) == DAY


@pytest.mark.parametrize('case', ['legacy', 'manual', 'missing', 'changed', 'identity'])
def test_refresh_refuses_unsafe_changes(tmp_path, case):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    path = files(tmp_path, [b'one'])[0]
    if case == 'legacy': store.upsert(profile())
    else: import_files(store, [path], loader=lambda p: [profile()])
    if case == 'manual': store.upsert(profile(activity='work'))
    elif case == 'missing': store.list_archived_files(profile().profile_key)[0].unlink()
    elif case == 'changed': store.list_archived_files(profile().profile_key)[0].write_bytes(b'two')
    before = store.get(profile().profile_key)
    with pytest.raises(DddLoadError):
        refresh_profile(store, profile().profile_key, loader=lambda p: [profile(OTHER) if case=='identity' else profile()])
    assert store.get(profile().profile_key) == before


def test_failed_archive_rolls_back_file_and_other_files_continue(tmp_path, monkeypatch):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    paths = files(tmp_path, [b'one', b'two'])
    original = store.archive_source_files
    def archive(key, paths):
        if key == profile().profile_key: raise OSError('Brak miejsca')
        return original(key, paths)
    monkeypatch.setattr(store, 'archive_source_files', archive)
    report = import_files(store, paths, loader=lambda p: [profile(CARD if p.read_bytes()==b'one' else OTHER)])
    assert len(report.errors) == 1 and report.imported == 1
    assert store.get(profile().profile_key) is None


def test_loader_parses_once_splits_vu_and_rejects_unassigned(tmp_path, monkeypatch):
    import ddd_analyzer.parser as parser
    path = files(tmp_path, [b'vu'])[0]
    calls=[]
    def parse(p):
        calls.append(p)
        return parse_tacho_result(vu_raw(), str(p), DddFileKind.VU)
    monkeypatch.setattr(parser, 'parse_ddd_file', parse)
    parts = load_ddd_profiles(path)
    assert len(calls) == 1 and {p.profile_key for p in parts} == {f'card:{CARD}', f'card:{OTHER}'}


def test_conflicting_overlap_imports_with_warning_without_overwrite(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    paths = files(tmp_path, [b'one', b'two'])
    report = import_files(store, paths, loader=lambda p: [profile(activity='availability' if p.read_bytes()==b'one' else 'rest')])
    assert not report.errors and report.merged == 1 and report.warnings
    assert store.get_data_issues(profile().profile_key)
    assert len(store.list_archived_files(profile().profile_key)) == 2
    assert store.get(profile().profile_key).segments[0].activity == 'availability'


def test_unknown_activity_never_becomes_rest():
    raw = card_raw(); raw['activities'][0]['changes'][1]['activity'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='Nieznana aktywność'):
        parse_tacho_result(raw, 'C.ddd', DddFileKind.CARD)


def test_vu_g2_activity_and_iw_decoder():
    from core.parser.vu_dispatcher import _decode_record, decode_card_iw
    rec = bytearray(131)
    rec[72:91] = bytes([1, 0x1b]) + CARD.encode() + bytes([2])
    rec[95:99] = struct.pack('>I', int(datetime(2026,7,6,1,tzinfo=timezone.utc).timestamp()))
    rec[102] = 0
    rec[103:107] = struct.pack('>I', int(datetime(2026,7,6,3,tzinfo=timezone.utc).timestamp()))
    raw = vu_raw()
    raw['card_iw_records'] = [decode_card_iw(bytes(rec))]
    change = _decode_record(0x01, struct.pack('>H', aci(1,60)))['activity']
    assert change['activity'] == 'AVAILABLE'
    raw['activities'][0]['changes'][1] = change
    result = parse_tacho_result(raw, 'G2.ddd', DddFileKind.VU)
    assert result.driver_analyses[0].driver.card_number == CARD
    assert result.driver_analyses[0].daily_summaries[0].availability_min == 30


def test_actual_corrupt_file_reported(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    path = files(tmp_path, [b'not a tachograph download'])[0]
    report = import_files(store, [path])
    assert len(report.errors) == 1 and path.name in report.errors[0]
    assert store.list_profiles() == []


def test_analyzer_displays_merged_profile_without_other_driver(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from ddd_analyzer.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    p = replace(profile(), source_kind='vu', vehicle_plate='WX1')
    win.load_profile(p)
    assert win._active.driver.card_number == CARD
    assert win._active.daily_summaries[0].availability_min == 30
    assert win._active.daily_summaries[0].work_min == 0
    win.close(); win.deleteLater(); app.processEvents()


def test_multi_picker_summary_keeps_template(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from brudnopis_ot.ui import main_window
    from brudnopis_ot.core import ddd_import
    app = QApplication.instance() or QApplication([])
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    template = store.create_template('Test', DAY)
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    paths = files(tmp_path, [b'one', b'two'])
    selected = []
    def picker(*args): selected.append(True); return [str(p) for p in paths], ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', picker)
    reports=[]
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: reports.append(args[2]))
    original = ddd_import.import_files
    monkeypatch.setattr(ddd_import, 'import_files', lambda st, pp, **kwargs: original(st, pp, loader=lambda p: [profile(CARD if p.read_bytes()==b'one' else OTHER)], **kwargs))
    win = main_window.MainWindow()
    win._apply_ddd_profile(template)
    win._add_ddd_file()
    assert selected and 'Zaimportowano: 2' in reports[-1]
    assert win._ddd_profile.profile_key == template.profile_key
    assert len(store.list_profiles()) == 3
    win.close(); win.deleteLater(); app.processEvents()


def test_card_and_vu_same_driver_merge_one_profile(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    paths = files(tmp_path, [b'card', b'vu'])
    def loader(p):
        return [profile() if p.read_bytes()==b'card' else replace(profile(start=90, end=120), source_kind='vu', vehicle_plate='WX1')]
    result = import_files(store, paths, loader=loader)
    assert not result.errors and len(store.list_profiles()) == 1
    assert store.get(profile().profile_key).segments == [RawSegment(DAY, 'availability', 60, 120)]


def test_multi_driver_file_transaction_rolls_back_all_profiles(tmp_path, monkeypatch):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    path = files(tmp_path, [b'vu'])[0]
    original = store.archive_source_files
    def archive(key, paths):
        if key == profile(OTHER).profile_key: raise OSError('Brak miejsca')
        return original(key, paths)
    monkeypatch.setattr(store, 'archive_source_files', archive)
    result = import_files(store, [path], loader=lambda p: [profile(), profile(OTHER)])
    assert len(result.errors) == 1 and result.imported == 0
    assert store.list_profiles() == []


def test_raw_ewidentor_serialized_snapshot_keeps_availability(tmp_path):
    from ewidentor.core.models import Driver
    from ewidentor.core.store import EwidentorStore
    from dataclasses import asdict
    store = EwidentorStore(tmp_path/'ewi.sqlite')
    driver = store.upsert_driver(Driver(id=None, full_name='Test'))
    day = build_day_from_segments(DAY, profile().segments, night_start=time(), night_end=time(4), plan_min=0)
    snapshot = asdict(day); snapshot['day'] = str(DAY)
    store.save_month_closure(driver.id, 2026, 7, {'days': [snapshot]})
    with store._connect() as conn:
        saved = json.loads(conn.execute('SELECT snapshot_json FROM month_closures').fetchone()[0])['days'][0]
    assert saved['availability_min'] == 30 and saved['work_min'] == saved['duty_50_min'] == 0


@pytest.mark.parametrize('activity', ['availability', 'rest', 'work', 'drive'])
def test_legacy_overlap_keeps_history_adds_uncovered_and_deduplicates(tmp_path, activity):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    old = replace(profile(activity=activity), title='Moja nazwa')
    store.upsert(old)
    path = files(tmp_path, [b'legacy'])[0]
    loader = lambda p: [profile(start=75, end=120)]
    report = import_files(store, [path], loader=loader)
    assert report.merged == 1 and not report.errors
    assert bool(report.warnings) == (activity != 'availability')
    saved = store.get(old.profile_key)
    minutes = {m: s.activity for s in saved.segments for m in range(s.start_minute, s.end_minute)}
    assert all(minutes[m] == activity for m in range(60, 90))
    assert all(minutes[m] == 'availability' for m in range(90, 120))
    assert saved.title == old.title
    assert import_files(store, [path], loader=loader).skipped == 1
    assert len(store.list_archived_files(old.profile_key)) == 1
    with pytest.raises(DddLoadError):
        refresh_profile(store, old.profile_key, loader=loader)
    # A later import must retain the same conservative policy.
    second = files(tmp_path, [b'next'])[0]
    result = import_files(store, [second], loader=lambda p: [profile(activity='drive', start=60, end=150)])
    assert not result.errors
    latest = store.get(old.profile_key)
    assert all(any(s.start_minute <= m < s.end_minute and s.activity == activity for s in latest.segments) for m in range(60, 90))


def test_legacy_preserves_auxiliary_manual_records(tmp_path):
    from brudnopis_ot.core.ddd_file_loader import PlaceRecord
    point = PlaceRecord(datetime(2026, 7, 6, 1, tzinfo=timezone.utc), 'START', 'PL', 123)
    old = replace(profile(), places=(point,))
    incoming = replace(profile(start=75, end=120), places=(replace(point, country_code='DE'),))
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    store.upsert(old)
    report = import_files(store, files(tmp_path, [b'places']), loader=lambda p: [incoming])
    assert not report.errors
    assert store.get(old.profile_key).places == (point,)
