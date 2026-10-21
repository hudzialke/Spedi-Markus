from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
import pytest

from ddd_analyzer.adapter import parse_tacho_result, clip_segments_to_cutoff
from ddd_analyzer.models import DddFileKind, ActivitySegment, ActivityType
from brudnopis_ot.core.ddd_import import preview_profile_end, apply_profile_end, refresh_profile
from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.ddd_file_loader import DddLoadResult, DddLoadError
from brudnopis_ot.core.hudonizator_reader import RawSegment

DAY = date(2026, 9, 14)


def raw_card():
    return {'activities': [{'date': str(DAY), 'changes': [
        {'time': '00:00', 'activity': 'REST'},
        {'time': '06:20', 'activity': 'WORK'},
        {'time': '06:30', 'activity': 'DRIVE'}]}]}


@pytest.mark.parametrize('download', [None, '2026-09-10T09:17:35Z'])
def test_missing_or_stale_download_does_not_invent_final_activity(download):
    raw = raw_card()
    if download:
        raw['card_downloads'] = [{'download_time': download}]
    result = parse_tacho_result(raw, 'C_20991231_2359.ddd', DddFileKind.CARD)
    assert max(s.end_minute for s in result.segments) == 390
    assert result.daily_summaries[0].drive_min == 0
    assert result.daily_summaries[0].work_min == 10


def test_current_download_confirms_only_complete_minutes():
    raw = raw_card()
    raw['card_downloads'] = [{'download_time': '2026-09-14T08:35:29+02:00'}]
    result = parse_tacho_result(raw, 'renamed.ddd', DddFileKind.CARD)
    assert max(s.end_minute for s in result.segments) == 395
    assert result.daily_summaries[0].drive_min == 5


def test_midnight_cutoff_removes_next_day():
    segments = [ActivitySegment(DAY - timedelta(days=1), ActivityType.REST, 60, 1440),
                ActivitySegment(DAY, ActivityType.REST, 0, 1440)]
    result = clip_segments_to_cutoff(segments, datetime(2026, 9, 14, tzinfo=timezone.utc))
    assert result == segments[:1]


def test_vu_end_applies_to_owned_slot_as_well_as_vehicle():
    raw = raw_card()
    for c in raw['activities'][0]['changes']:
        c.update(slot='First', card_inserted=True)
    raw['metadata'] = {'is_vu': True, 'current_datetime': '2026-09-14T06:35:00Z'}
    raw['card_iw_records'] = [{'card': {'card_type': 1, 'card_number': '1234567890123456'},
                             'card_slot': 0, 'insertion_time': '2026-09-14T00:00:00Z',
                             'withdrawal_time': '2026-09-14T08:00:00Z'}]
    result = parse_tacho_result(raw, 'M.ddd', DddFileKind.VU)
    assert max(s.end_minute for s in result.segments) == 395
    assert max(s.end_minute for s in result.driver_analyses[0].segments) == 395


def test_parsing_same_bytes_ignores_computer_clock(tmp_path, monkeypatch):
    from ddd_analyzer import parser
    parser.ensure_parser_path()
    from app.engine import TachoParser
    monkeypatch.setattr(TachoParser, 'parse', lambda self: raw_card())
    path = tmp_path/'any.ddd'
    path.write_bytes(b'fixture')
    before = parser.parse_ddd_file(path)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(parser, 'datetime', Later)
    after = parser.parse_ddd_file(path)
    assert before.segments == after.segments
    assert max(s.end_minute for s in after.segments) == 390


def profile(end=1440):
    return DddLoadResult([RawSegment(DAY, 'drive', 390, end)], 'Mój profil', 'Test',
                         '1234567890123456', None, 'card', ('source.ddd',), DAY, DAY)


def test_end_preview_is_read_only_and_confirmed_change_has_backup(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    old = store.upsert(profile())
    before, candidate, cutoff, minutes = preview_profile_end(store, old.profile_key, 'source.ddd', loader=lambda p: [profile(395)])
    assert store.get(old.profile_key) == old
    assert cutoff == datetime(2026, 9, 14, 6, 35)
    assert minutes == 1045
    saved = apply_profile_end(store, before, candidate)
    assert saved.segments == profile(395).segments
    from ewidentor.core.brudnopis_import import load_month_segments
    exported = load_month_segments(store.path, old.profile_key, 2026, 9)
    assert max(s.end_minute for s in exported) == 395
    with store._connect() as con:
        assert con.execute('SELECT count(*) FROM ddd_profile_backups').fetchone()[0] == 1
    with pytest.raises(DddLoadError):
        refresh_profile(store, old.profile_key)


def test_end_preview_rejects_other_driver_and_stale_confirmation(tmp_path):
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    old = store.upsert(profile())
    with pytest.raises(DddLoadError):
        preview_profile_end(store, old.profile_key, 'other.ddd', loader=lambda p: [replace(profile(), card_number='2234567890123456')])
    before, candidate, _, _ = preview_profile_end(store, old.profile_key, 'source.ddd', loader=lambda p: [profile(395)])
    changed = store.upsert(profile(400))
    with pytest.raises(DddLoadError):
        apply_profile_end(store, before, candidate)
    assert store.get(old.profile_key) == changed


def test_delete_manual_block_persists_for_template(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from brudnopis_ot.ui import main_window
    from brudnopis_ot.core.models import Block, ActivityKind
    app = QApplication.instance() or QApplication([])
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    template = store.create_template('Test', DAY)
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    win = main_window.MainWindow()
    win._apply_ddd_profile(template)
    block = Block(kind=ActivityKind.WORK, start=datetime(2026, 9, 14, 6), end=datetime(2026, 9, 14, 7))
    win._scenario.add_block(block)
    win._scenario.anchor_block_id = block.id
    win._mark_dirty()
    win.grid.delete_block_requested.emit(block.id)
    assert win._scenario.get_block(block.id) is None
    assert win._scenario.anchor_block_id is None
    assert store.load_template_scenario(template.profile_key).get_block(block.id) is None
    win.close(); win.deleteLater(); app.processEvents()


def test_cancel_end_correction_does_not_change_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from brudnopis_ot.ui import main_window
    from brudnopis_ot.core import ddd_import
    app = QApplication.instance() or QApplication([])
    store = DddProfileStore(tmp_path/'ddd.sqlite')
    old = store.upsert(profile())
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: ('source.ddd', ''))
    preview = preview_profile_end(store, old.profile_key, 'source.ddd', loader=lambda p: [profile(395)])
    monkeypatch.setattr(ddd_import, 'preview_profile_end', lambda *args: preview)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    win = main_window.MainWindow()
    win._apply_ddd_profile(old)
    win._check_ddd_end()
    assert store.get(old.profile_key) == old
    win.close(); win.deleteLater(); app.processEvents()
