from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
import json
import sys
import types

import pytest

from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.ddd_import import import_files
from brudnopis_ot.core.ddd_file_loader import DddLoadResult
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.models import ActivityKind
from brudnopis_ot.core.serialize import scenario_to_dict

DAY = date(2026, 9, 7)


def profile(card='CARD1', segments=None):
    return DddLoadResult(segments or [RawSegment(DAY, 'work', 60, 120), RawSegment(DAY, 'drive', 120, 180)],
                         'Test', 'Test', card, None, 'card', ('C.ddd',), DAY, DAY)


def test_correction_anchor_and_original_survive_reopen(tmp_path):
    path = tmp_path / 'profiles.sqlite'
    store = DddProfileStore(path)
    p = store.upsert(profile())
    source = store.load_ddd_scenario(p.profile_key, original=True)
    edited = deepcopy(source)
    edited.blocks[0].kind = ActivityKind.REST
    edited.set_week1_anchor(edited.blocks[1].id)
    store.save_ddd_scenario(p.profile_key, edited)
    reopened = DddProfileStore(path)
    assert scenario_to_dict(reopened.load_ddd_scenario(p.profile_key)) == scenario_to_dict(edited)
    assert reopened.load_ddd_scenario(p.profile_key, original=True).blocks[0].kind == ActivityKind.WORK
    assert reopened.get(p.profile_key).segments == p.segments


def test_new_ddd_extends_history_without_losing_correction_or_deleted_block(tmp_path):
    store = DddProfileStore(tmp_path / 'p.sqlite')
    p = store.upsert(profile())
    edited = store.load_ddd_scenario(p.profile_key)
    edited.blocks[0].kind = ActivityKind.REST
    edited.anchor_block_id = edited.blocks[0].id
    edited.blocks.pop(1)
    store.save_ddd_scenario(p.profile_key, edited)
    incoming = profile(segments=[RawSegment(DAY, 'work', 60, 120), RawSegment(DAY, 'drive', 120, 240),
                                 RawSegment(DAY + timedelta(days=1), 'drive', 60, 180)])
    path = tmp_path / 'new.ddd'; path.write_bytes(b'next')
    report = import_files(store, [path], loader=lambda _: [incoming])
    assert not report.errors
    loaded = store.load_ddd_scenario(p.profile_key)
    assert any(b.kind == ActivityKind.REST for b in loaded.blocks)
    assert loaded.anchor_block_id == edited.anchor_block_id
    assert any(b.start == datetime(2026, 9, 7, 3) and b.end == datetime(2026, 9, 7, 4) for b in loaded.blocks)
    assert any(b.start.date() == DAY + timedelta(days=1) for b in loaded.blocks)


def test_raw_manual_changes_do_not_block_import_and_migrate_to_corrections(tmp_path):
    store = DddProfileStore(tmp_path / 'p.sqlite')
    first = tmp_path / 'first.ddd'; first.write_bytes(b'first')
    import_files(store, [first], loader=lambda _: [profile()])
    old = store.get(profile().profile_key)
    store.upsert(replace(old, segments=[RawSegment(DAY, 'rest', 60, 120), old.segments[1]]))
    second = tmp_path / 'second.ddd'; second.write_bytes(b'second')
    incoming = profile(segments=[*old.segments, RawSegment(DAY + timedelta(days=1), 'work', 60, 120)])
    result = import_files(store, [second], loader=lambda _: [incoming])
    assert result.merged == 1 and not result.errors
    assert store.load_ddd_scenario(old.profile_key).blocks[0].kind == ActivityKind.REST
    assert store.load_ddd_scenario(old.profile_key, original=True).blocks[0].kind == ActivityKind.WORK
    assert len(store.list_archived_files(old.profile_key)) == 2


def test_conflict_and_partial_corruption_imported_with_persistent_issues(tmp_path):
    store = DddProfileStore(tmp_path / 'p.sqlite')
    store.upsert(profile())
    incoming = profile(segments=[RawSegment(DAY, 'rest', 60, 90), RawSegment(DAY, 'work', 180, 240),
                                 RawSegment(DAY, 'unknown', 240, 300)])
    path = tmp_path / 'bad-part.ddd'; path.write_bytes(b'partial')
    report = import_files(store, [path], loader=lambda _: [incoming])
    assert report.merged == 1 and not report.errors and report.warnings
    assert RawSegment(DAY, 'work', 180, 240) in store.get(incoming.profile_key).segments
    assert len(DddProfileStore(store.path).get_data_issues(incoming.profile_key)) == 2
    again = import_files(store, [path], loader=lambda _: [incoming])
    assert again.skipped == 1


def test_parser_order_is_not_mistaken_for_manual_correction(tmp_path):
    store = DddProfileStore(tmp_path / 'p.sqlite')
    first = tmp_path / 'first.ddd'; first.write_bytes(b'first')
    import_files(store, [first], loader=lambda _: [profile()])
    with store._connect() as conn:
        row = conn.execute('SELECT snapshot FROM ddd_baselines').fetchone()
        data = json.loads(row['snapshot'])
        data['segments'].reverse()
        conn.execute('UPDATE ddd_baselines SET snapshot=?', (json.dumps(data),))
    second = tmp_path / 'second.ddd'; second.write_bytes(b'second')
    result = import_files(store, [second], loader=lambda _: [profile()])
    assert not result.errors and not result.warnings and result.merged == 1
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM ddd_scenario_edits').fetchone()[0] == 0


@pytest.fixture
def window(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
    app = QApplication.instance() or QApplication([])
    stub = types.ModuleType('ddd_analyzer.ui.main_window'); stub.MainWindow = QWidget
    monkeypatch.setitem(sys.modules, 'ddd_analyzer.ui.main_window', stub)
    from brudnopis_ot.ui import main_window
    store = DddProfileStore(tmp_path / 'ui.sqlite')
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: pytest.fail(str(args[1:])))
    win = main_window.MainWindow()
    yield win, store
    win.close(); win.deleteLater(); app.processEvents()


def test_ui_switch_reopen_original_axis_and_hidden_history(window):
    win, store = window
    p = store.upsert(profile(segments=[RawSegment(DAY, 'work', 60, 120),
                                      RawSegment(DAY + timedelta(days=7), 'drive', 60, 120)]))
    other = store.upsert(profile('CARD2'))
    store.set_cycle_start(p.profile_key, DAY)
    win._apply_ddd_profile(p)
    win._scenario.blocks[0].kind = ActivityKind.REST
    win._mark_dirty()
    win._set_week1_anchor(win._scenario.blocks[1].id)
    expected = scenario_to_dict(store.load_ddd_scenario(p.profile_key))
    win._apply_ddd_profile(other)
    win._apply_ddd_profile(p)
    assert win._scenario.anchor_block_id == expected['anchor_block_id']
    win._ddd_view.setCurrentIndex(1)
    assert not win.grid.editable
    assert store.load_ddd_scenario(p.profile_key, original=True).blocks[0].kind == ActivityKind.WORK
    win._ddd_view.setCurrentIndex(0)
    win._on_cycle_date_changed(win._qdate(DAY))
    assert win._scenario.blocks[0].kind == ActivityKind.REST
    assert len(win._scenario.blocks) == 2
    assert not win._dirty


def test_lens_selects_block_then_sets_anchor(window):
    from PySide6.QtWidgets import QApplication
    from brudnopis_ot.ui.timeline_lens import TimelineLensDialog
    win, store = window
    p = store.upsert(profile())
    win._apply_ddd_profile(p)
    dialog = TimelineLensDialog(win.grid, [(datetime(2026, 9, 7), datetime(2026, 9, 8))])
    block = win._scenario.blocks[1]
    dialog.select_block(block.id)
    assert not dialog.selected_id
    dialog.anchor_button.click()
    assert dialog.selected_id == block.id and dialog.selected_action == 'anchor'
    win._set_week1_anchor(dialog.selected_id)
    assert store.load_ddd_scenario(p.profile_key).anchor_block_id == block.id
