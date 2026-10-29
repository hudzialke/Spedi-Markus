"""Storage lifetime, batch rollback and equivalence of indexed country totals."""
from dataclasses import asdict
from datetime import date, datetime, timedelta
import sqlite3
import threading

import pytest

from ewidentor.core.store import EwidentorStore
from ewidentor.core.models import Driver
from ewidentor.core.milo import MiloStay
from ewidentor.core.milo_rates import OpType


def stay(driver, day=1):
    return MiloStay(None, driver.id, date(2026, 10, day), 'DE', 120, OpType.BILATERAL, 'Synthetic')


def test_ewidentor_connections_are_closed_after_context(tmp_path):
    store = EwidentorStore(tmp_path / 'test.sqlite')
    with store._connect() as connection:
        assert connection.execute('SELECT 1').fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        connection.execute('SELECT 1')


def test_batch_reuses_one_connection_and_commits_complete_set(tmp_path, monkeypatch):
    store = EwidentorStore(tmp_path / 'test.sqlite')
    driver = store.upsert_driver(Driver(None, 'Synthetic'))
    opened = []
    original = sqlite3.connect
    def counted(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', counted)
    with store.transaction() as connection:
        for day in range(1, 32):
            store.upsert_milo_stay(stay(driver, day))
        with store._connect() as nested:
            assert nested is connection
    assert len(opened) == 1
    assert len(store.list_milo_stays(driver.id, 2026, 10)) == 31


def test_batch_rolls_back_all_new_rows_and_preserves_existing_manual_entry(tmp_path):
    store = EwidentorStore(tmp_path / 'test.sqlite')
    driver = store.upsert_driver(Driver(None, 'Synthetic'))
    existing = store.upsert_milo_stay(stay(driver))
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with store.transaction():
            store.upsert_milo_stay(stay(driver, 2))
            store.upsert_milo_stay(stay(driver, 3))
            raise RuntimeError('synthetic failure')
    actual = store.list_milo_stays(driver.id, 2026, 10)
    assert len(actual) == 1 and asdict(actual[0]) == asdict(existing)


def test_transactions_do_not_share_sqlite_connections_between_threads(tmp_path):
    store = EwidentorStore(tmp_path / 'test.sqlite')
    observations, failures = [], []
    with store.transaction() as main_connection:
        def read():
            try:
                with store._connect() as connection:
                    observations.append(connection is not main_connection)
                    observations.append(connection.execute('SELECT 42').fetchone()[0] == 42)
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=read)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
    assert observations == [True, True] and not failures


def test_country_interval_index_preserves_all_overlaps_and_first_seen():
    from ewidentor.core.brudnopis_import import RawSegment
    from ewidentor.core.country_minutes import _minutes_from_intervals
    day = date(2026, 10, 5)
    midnight = datetime.combine(day, datetime.min.time())
    segments = [RawSegment(day, 'drive', 60, 180), RawSegment(day, 'availability', 180, 240),
                RawSegment(day, 'rest', 0, 1440), RawSegment(day, 'work', 300, 360)]
    intervals = [(midnight+timedelta(minutes=120), midnight+timedelta(minutes=300), 'DE'),
                 (midnight, midnight+timedelta(minutes=1440), 'PL'),
                 (midnight+timedelta(minutes=240), midnight+timedelta(minutes=300), 'NL')]
    actual = _minutes_from_intervals(segments, intervals)
    assert [(item.country_code, item.minutes, item.first_seen) for item in actual] == [
        ('PL', 240, midnight+timedelta(minutes=60)),
        ('DE', 120, midnight+timedelta(minutes=120))]


def test_ddd_identity_parsing_runs_in_background_and_keeps_partial_success(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from ewidentor.ui import main_window as ui
    from ewidentor.core.ddd_identity import DddIdentity, DddIdentityError
    app = QApplication.instance() or QApplication([])
    store = EwidentorStore(tmp_path / 'test.sqlite')
    monkeypatch.setattr(ui, 'EwidentorStore', lambda: store)
    monkeypatch.setattr(ui, 'ddd_analyzer_available', lambda: True)
    monkeypatch.setattr(ui.QFileDialog, 'getOpenFileNames', lambda *a: (['good.ddd', 'bad.ddd'], ''))
    messages, threads, ticks = [], [], []
    monkeypatch.setattr(ui.QMessageBox, 'information', lambda *args: messages.append(args[2]))
    def load(path):
        threads.append(threading.get_ident())
        if path == 'bad.ddd':
            raise DddIdentityError('Synthetic error')
        return DddIdentity('Synthetic', 'CARD', None, 'card', path)
    monkeypatch.setattr(ui, 'read_ddd_identity', load)
    window = ui.MainWindow(brudnopis_db=tmp_path / 'no-source.sqlite')
    try:
        QTimer.singleShot(0, lambda: ticks.append(True))
        window.import_ddd_identity()
        assert len(store.list_drivers()) == 1
        assert store.list_drivers()[0].card_number == 'CARD'
        assert ticks and all(thread != threading.get_ident() for thread in threads)
        assert 'błędy: 1' in messages[-1] and 'Synthetic error' in messages[-1]
    finally:
        window._autoload_timer.stop()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_watch_and_month_readers_close_connections_without_waiting_for_gc(tmp_path, monkeypatch):
    from brudnopis_ot.core.ddd_store import DddProfileStore
    from spedi.weekly_watch import WatchStore, WatchOptions
    from ewidentor.core.brudnopis_import import (
        list_brudnopis_profiles, load_month_segments, load_month_places, load_month_border_crossings)
    source = DddProfileStore(tmp_path / 'source.sqlite')
    driver = source.create_template('Synthetic', date(2026, 10, 5))
    connections = []
    original = sqlite3.connect
    def tracked(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', tracked)
    watch = WatchStore(source.path)
    watch.save(driver.profile_key, WatchOptions(selected=True))
    assert watch.get(driver.profile_key).selected
    list_brudnopis_profiles(source.path)
    load_month_segments(source.path, driver.profile_key, 2026, 10)
    load_month_places(source.path, driver.profile_key, 2026, 10)
    load_month_border_crossings(source.path, driver.profile_key, 2026, 10)
    assert len(connections) == 7
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')
