from datetime import date, datetime, timedelta
from dataclasses import replace
import sqlite3
import sys
import types

import pytest

from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.ddd_blocks import scenario_from_segments
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.models import ActivityKind, Block
from brudnopis_ot.core.serialize import scenario_to_dict
from brudnopis_ot.core.validator import validate_scenario

MONDAY = date(2026, 7, 27)
START = datetime(2026, 7, 27, 9)


def test_template_create_reload_rename_delete(tmp_path):
    path = tmp_path / 'profiles.sqlite'
    store = DddProfileStore(path)
    profile = store.create_template('  Jan  ', MONDAY)
    other = store.create_template('Jan', MONDAY)
    assert profile.profile_key != other.profile_key
    loaded = DddProfileStore(path).get(profile.profile_key)
    assert loaded == profile
    assert loaded.driver_name == 'Jan'
    assert loaded.source_kind == 'template'
    assert loaded.card_number is None and loaded.vehicle_plate is None
    assert not loaded.filenames and not loaded.segments
    assert 'Ręczny profil' in loaded.profile_label
    scenario = store.load_template_scenario(profile.profile_key)
    scenario.title = 'Nowa nazwa'
    store.save_template_scenario(profile.profile_key, scenario)
    assert store.get(profile.profile_key).driver_name == 'Nowa nazwa'
    store.delete_template(profile.profile_key)
    assert store.get(profile.profile_key) is None
    assert store.get(other.profile_key) is not None


def test_blank_name_rejected(tmp_path):
    store = DddProfileStore(tmp_path / 't.sqlite')
    with pytest.raises(ValueError):
        store.create_template('  ', MONDAY)
    assert not store.list_profiles()


def test_blocks_and_anchor_survive_reopen_exactly(tmp_path):
    path = tmp_path / 't.sqlite'
    store = DddProfileStore(path)
    profile = store.create_template('Jan', MONDAY)
    scenario = store.load_template_scenario(profile.profile_key)
    for i, kind in enumerate([ActivityKind.DRIVE, ActivityKind.DRIVE, *ActivityKind]):
        scenario.add_block(Block(kind, START + timedelta(hours=i), START + timedelta(hours=i+1),
                                 label=f'Blok {i}', country_start='PL'))
    scenario.set_week1_anchor(scenario.blocks[1].id)
    store.save_template_scenario(profile.profile_key, scenario)
    reopened = DddProfileStore(path).load_template_scenario(profile.profile_key)
    assert scenario_to_dict(reopened) == scenario_to_dict(scenario)
    assert reopened.get_block(reopened.anchor_block_id) is not None
    assert DddProfileStore(path).get_cycle_start(profile.profile_key) == MONDAY


def test_date_axis_persists(tmp_path):
    store = DddProfileStore(tmp_path / 't.sqlite')
    profile = store.create_template('Jan', MONDAY)
    scenario = store.load_template_scenario(profile.profile_key)
    scenario.set_axis_monday(MONDAY + timedelta(days=7))
    store.save_template_scenario(profile.profile_key, scenario)
    assert store.load_template_scenario(profile.profile_key).week_start == MONDAY + timedelta(days=7)
    assert store.get_cycle_start(profile.profile_key) == MONDAY + timedelta(days=7)


@pytest.mark.parametrize('late_rest', [False, True])
def test_deadline_matches_ddd_engine_after_reopen(tmp_path, late_rest):
    store = DddProfileStore(tmp_path / 't.sqlite')
    profile = store.create_template('Jan', MONDAY)
    manual = store.load_template_scenario(profile.profile_key)
    manual.add_block(Block(ActivityKind.DRIVE, START, START + timedelta(hours=8)))
    ddd = scenario_from_segments([RawSegment(MONDAY, 'drive', 540, 1020)], title='DDD', from_date=MONDAY)
    for scenario in (manual, ddd):
        drive = next(b for b in scenario.blocks if b.kind == ActivityKind.DRIVE)
        scenario.set_week1_anchor(drive.id)
        if late_rest:
            scenario.add_block(Block(ActivityKind.REST, START - timedelta(hours=45), START))
            scenario.add_block(Block(ActivityKind.REST, START + timedelta(days=7), START + timedelta(days=9)))
    store.save_template_scenario(profile.profile_key, manual)
    result = validate_scenario(store.load_template_scenario(profile.profile_key))
    imported = validate_scenario(ddd)
    assert result.six_day_windows == imported.six_day_windows
    assert any(w.start == START and w.end == START + timedelta(hours=144) for w in result.six_day_windows)
    assert [(f.code, f.severity) for f in result.findings] == [(f.code, f.severity) for f in imported.findings]
    if late_rest:
        assert any(f.code == 'six_x_24h' for f in result.findings)


def test_ddd_same_name_does_not_merge_into_template(tmp_path):
    store = DddProfileStore(tmp_path / 't.sqlite')
    manual = store.create_template('Jan', MONDAY)
    incoming = replace(manual, source_kind='card', template_id=None, card_number='CARD1', filenames=('C.ddd',))
    saved, status = store.resolve_and_merge(incoming)
    assert status == 'created'
    assert saved.profile_key == 'card:CARD1'
    assert store.get(manual.profile_key) == manual
    store.delete_template(saved.profile_key)
    assert store.get(saved.profile_key) == saved


def test_migration_preserves_legacy_profile(tmp_path):
    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE profiles (
            profile_key TEXT PRIMARY KEY, source_kind TEXT NOT NULL, card_number TEXT,
            vehicle_plate TEXT, driver_name TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
            filenames_json TEXT NOT NULL DEFAULT '[]', date_from TEXT, date_to TEXT, updated_at TEXT NOT NULL)''')
        conn.execute("INSERT INTO profiles (profile_key, source_kind, card_number, driver_name, updated_at) VALUES ('card:1', 'card', '1', 'Old', '2026-01-01')")
    store = DddProfileStore(path)
    assert store.get('card:1').driver_name == 'Old'
    profile = store.create_template('New', MONDAY)
    assert len(DddProfileStore(path).list_profiles()) == 2
    assert store.load_template_scenario(profile.profile_key).week_start == MONDAY


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
    app = QApplication.instance() or QApplication([])
    stub = types.ModuleType('ddd_analyzer.ui.main_window')
    stub.MainWindow = QWidget
    monkeypatch.setitem(sys.modules, 'ddd_analyzer.ui.main_window', stub)
    from brudnopis_ot.ui import main_window
    store = DddProfileStore(tmp_path / 'ui.sqlite')
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    def unexpected(*args, **kwargs):
        pytest.fail(f'Unexpected message: {args[1:]}')
    monkeypatch.setattr(QMessageBox, 'critical', unexpected)
    monkeypatch.setattr(QMessageBox, 'information', unexpected)
    win = main_window.MainWindow()
    yield win, store, main_window
    win.close()
    win.deleteLater()
    app.processEvents()


def test_ui_create_edit_switch_reopen_anchor_and_delete(window, monkeypatch):
    win, store, ui = window
    monkeypatch.setattr(ui.QInputDialog, 'getText', lambda *a, **k: ('Jan', True))
    win._new_template()
    key = win._ddd_profile.profile_key
    assert win._tabs.widget(1) is win._template_analysis
    block = Block(ActivityKind.DRIVE, START, START + timedelta(hours=8))
    class Editor:
        def __init__(self, *a, **k): pass
        def exec(self): return True
        def result_block(self): return block
    monkeypatch.setattr(ui, 'BlockEditorDialog', Editor)
    win._new_block_at(START)
    win._set_week1_anchor(block.id)
    win._save()
    win._new_scenario()
    assert win._ddd_combo.findData(key) >= 0
    win._ddd_combo.setCurrentIndex(win._ddd_combo.findData(key))
    assert win._scenario.anchor_block_id == block.id
    assert win._scenario.blocks[0] == block
    assert validate_scenario(win._scenario).six_day_windows[0].end == START + timedelta(hours=144)
    monkeypatch.setattr(ui.QInputDialog, 'getText', lambda *a, **k: ('Jan 2', True))
    win._rename_template()
    assert store.get(key).driver_name == 'Jan 2'
    monkeypatch.setattr(ui.QMessageBox, 'question', lambda *a, **k: ui.QMessageBox.StandardButton.Yes)
    win._delete_template()
    assert store.get(key) is None


def test_ui_cycle_date_keeps_manual_blocks(window):
    win, store, ui = window
    profile = store.create_template('Jan', MONDAY)
    win._apply_ddd_profile(profile)
    block = Block(ActivityKind.DRIVE, START, START + timedelta(hours=8))
    win._scenario.add_block(block)
    win._mark_dirty()
    win._on_cycle_date_changed(ui.QDate(2026, 7, 27))
    assert store.load_template_scenario(profile.profile_key).blocks == [block]
    win._on_cycle_date_changed(ui.QDate(2026, 8, 3))
    assert not win._scenario.blocks
    assert store.load_template_scenario(profile.profile_key).blocks == [block]
    win._apply_ddd_profile(store.get(profile.profile_key))
    win._on_cycle_date_changed(ui.QDate(2026, 7, 27))
    assert win._scenario.blocks == [block]


def test_ui_edit_and_delete_block_persist(window, monkeypatch):
    win, store, ui = window
    profile = store.create_template('Jan', MONDAY)
    win._apply_ddd_profile(profile)
    block = Block(ActivityKind.DRIVE, START, START + timedelta(hours=8))
    win._scenario.add_block(block)
    win._mark_dirty()
    win._set_week1_anchor(block.id)
    updated = replace(block, end=START + timedelta(hours=9))
    class Editor:
        delete_requested = False
        def __init__(self, *a, **k): pass
        def exec(self): return True
        def result_block(self): return updated
    monkeypatch.setattr(ui, 'BlockEditorDialog', Editor)
    win._edit_block(block.id)
    assert store.load_template_scenario(profile.profile_key).blocks == [updated]
    Editor.delete_requested = True
    win._edit_block(block.id)
    saved = store.load_template_scenario(profile.profile_key)
    assert not saved.blocks and saved.anchor_block_id is None


def test_ui_switch_back_to_ddd_restores_analysis(window):
    win, store, ui = window
    profile = store.create_template('Jan', MONDAY)
    win._apply_ddd_profile(profile)
    incoming = replace(profile, source_kind='card', template_id=None, card_number='CARD',
                       filenames=('C.ddd',), segments=[RawSegment(MONDAY, 'drive', 540, 1020)],
                       date_from=MONDAY, date_to=MONDAY)
    store.upsert(incoming)
    win._apply_ddd_profile(incoming)
    assert win._tabs.widget(1) is win._analyzer
    assert not win._is_template()
    assert any(b.kind == ActivityKind.DRIVE for b in win._scenario.blocks)


def test_import_picker_excludes_template_targets(window):
    win, store, ui = window
    profile = store.create_template('Jan', MONDAY)
    incoming = replace(profile, source_kind='card', template_id=None, card_number='CARD')
    assert win._pick_ddd_profile([profile]) is None
    assert win._pick_ddd_profile([profile, incoming]) == incoming


@pytest.mark.parametrize('kind', list(ActivityKind))
def test_real_block_editor_activity_roundtrip(window, monkeypatch, kind):
    win, store, ui = window
    profile = store.create_template('Edytor', MONDAY)
    win._apply_ddd_profile(profile)

    def accept_editor(dialog):
        dialog.kind.setCurrentIndex(dialog.kind.findData(kind.value))
        dialog._accept()
        return dialog.DialogCode.Accepted

    # Keep the real QComboBox and result_block; only bypass modal user interaction.
    monkeypatch.setattr(ui.BlockEditorDialog, 'exec', accept_editor)
    win._new_block_at(START)
    block = win._scenario.blocks[0]
    assert isinstance(block.kind, ActivityKind)
    assert block.kind == kind
    win._save()
    assert store.load_template_scenario(profile.profile_key).blocks == [block]
    win._edit_block(block.id)
    assert store.load_template_scenario(profile.profile_key).blocks[0].kind == kind
