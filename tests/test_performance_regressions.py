"""Correctness and work bounds for the desktop performance improvements."""
from dataclasses import replace
from datetime import date, datetime, timedelta
import threading

import pytest

from brudnopis_ot.core.ddd_file_loader import DddLoadResult
from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.hudonizator_reader import RawSegment


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def card(segments, number='BENCH'):
    return DddLoadResult(segments, 'Synthetic', 'Synthetic', number, None, 'card', (),
                         min((s.day for s in segments), default=None),
                         max((s.day for s in segments), default=None))


def test_profile_picker_never_materializes_history(tmp_path, monkeypatch):
    store = DddProfileStore(tmp_path / 'profiles.sqlite')
    full = store.upsert(card([RawSegment(date(2026, 10, 1), 'drive', 60, 120)]))
    manual = store.create_template('Manual', date(2026, 10, 5))
    vu = store.upsert(replace(full, source_kind='vu', card_number=None, vehicle_plate='TEST'))
    def forbidden(*args):
        pytest.fail('A picker must not load raw activity rows')
    monkeypatch.setattr(store, '_load_row', forbidden)
    summaries = {p.profile_key: p for p in store.list_profile_summaries()}
    for profile in (full, manual, vu):
        item = summaries[profile.profile_key]
        assert item.profile_label == profile.profile_label
        assert (item.date_from, item.date_to) == (profile.date_from, profile.date_to)


def test_month_range_preserves_raw_and_confirmation_precedence(tmp_path):
    from ddd_analyzer.models import ActivityType, OpenActivity
    from brudnopis_ot.core.activity_ends import confirmed_segments
    from ewidentor.core.brudnopis_import import load_month_segments
    store = DddProfileStore(tmp_path / 'profiles.sqlite')
    start = datetime(2026, 9, 30, 23)
    pending = OpenActivity(ActivityType.AVAILABILITY, start)
    profile = store.upsert(replace(card([
        RawSegment(date(2024, 1, 1), 'rest', 0, 1440),
        RawSegment(date(2026, 9, 30), 'drive', 0, 1380),
        RawSegment(date(2026, 10, 1), 'drive', 120, 180),
        RawSegment(date(2026, 12, 31), 'rest', 0, 1440),
        RawSegment(date(2027, 1, 1), 'drive', 0, 60),
    ]), open_activity=pending))
    store.confirm_activity_end(profile.profile_key, pending, datetime(2026, 10, 2))
    with store._connect() as conn:
        confirmations = conn.execute('SELECT * FROM ddd_activity_ends').fetchall()
    expected = [*profile.segments, *confirmed_segments(profile.segments, confirmations, pending)]
    for year, month in ((2026, 9), (2026, 10), (2026, 11), (2026, 12), (2027, 1)):
        rows = load_month_segments(store.path, profile.profile_key, year, month)
        assert [(s.day, s.activity, s.start_minute, s.end_minute) for s in rows] == [
            (s.day, s.activity, s.start_minute, s.end_minute)
            for s in sorted(expected, key=lambda s: (s.day, s.start_minute, s.end_minute))
            if (s.day.year, s.day.month) == (year, month)]


def test_daily_summary_clips_multi_year_blocks_to_visible_range():
    from brudnopis_ot.core.daily_summary import build_daily_totals
    from brudnopis_ot.core.models import ActivityKind, Block
    totals = build_daily_totals([Block(ActivityKind.REST, datetime(2000, 1, 1), datetime(2030, 1, 1))],
                               range_start=date(2026, 10, 1), range_end=date(2026, 10, 31))
    assert len(totals) == 31 and all(t.rest_min == 1440 for t in totals)


def test_drive_analysis_reuses_daily_totals_and_preserves_seconds(monkeypatch):
    from brudnopis_ot.core import drive_limits
    from brudnopis_ot.core.models import ActivityKind, Block, Scenario
    calls = []
    original = drive_limits.build_daily_totals
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(drive_limits, 'build_daily_totals', counted)
    start = datetime(2026, 10, 5)
    scenario = Scenario(week_start=start.date(), weeks=2, blocks=[
        Block(ActivityKind.DRIVE, start+timedelta(days=i), start+timedelta(days=i, hours=10, seconds=30))
        for i in range(6)])
    result = drive_limits.analyze_drive_limits(scenario, [])
    assert len(calls) == 1
    assert {'drive_10h', 'drive_56h'} <= {f.code for f in result.findings}
    # The daily limit keeps minute rounding, while weekly hours keep seconds.
    assert not any(f.code == 'drive_10h_day' for f in result.findings)


def test_timeline_partial_repaint_keeps_hits_and_refreshes_mutated_data(app):
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtGui import QImage, QPainter, QRegion
    from brudnopis_ot.core.models import ActivityKind, Block, Scenario
    from brudnopis_ot.ui.week_grid import WeekGridWidget
    start = datetime(2026, 10, 5)
    blocks = [Block(ActivityKind.DRIVE, start+timedelta(days=i*7), start+timedelta(days=i*7, hours=2))
              for i in range(5)]
    scenario = Scenario(week_start=start.date(), weeks=5, blocks=blocks)
    grid = WeekGridWidget()
    grid.set_scenario(scenario)
    grid.resize(grid.minimumSize())
    image = QImage(grid.size(), QImage.Format.Format_ARGB32_Premultiplied)
    def paint(rect=None):
        painter = QPainter(image)
        try:
            grid.render(painter, QPoint(), QRegion(rect) if rect else QRegion())
        finally:
            painter.end()
    paint()
    expected = {identity: QRect(rect.toRect()) for identity, rect in grid._hit_blocks}
    paint(QRect(0, 0, grid.width(), 110))
    assert {identity: rect.toRect() for identity, rect in grid._hit_blocks} == expected
    grid.set_display_zone('UTC')
    paint()
    blocks[0].end += timedelta(hours=2)
    grid.set_scenario(scenario)
    paint()
    assert dict(grid._hit_blocks)[blocks[0].id].width() > 0
    assert set(dict(grid._hit_blocks)) == {b.id for b in blocks}
    grid.close()


def test_background_dialog_keeps_event_loop_alive_and_defers_close(app):
    from PySide6.QtCore import QTimer
    from spedi.background import OperationDialog
    entered, release = threading.Event(), threading.Event()
    threads, ticks = [], []
    def operation(task):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return 42
    dialog = OperationDialog(None, 'Synthetic', operation, cancellable=True)
    def exercise():
        ticks.append(True)
        assert entered.wait(1)
        dialog.reject()
        assert dialog.task.isRunning()
        assert dialog.task.isInterruptionRequested()
        release.set()
    QTimer.singleShot(10, exercise)
    dialog.task.start()
    try:
        dialog.exec()
    finally:
        release.set()
        assert dialog.task.wait(5000)
    assert ticks and threads != [threading.get_ident()]
    assert dialog.task.result == 42
    dialog.deleteLater()


def test_import_cancellation_commits_only_completed_files(tmp_path):
    from brudnopis_ot.core.ddd_import import import_files
    store = DddProfileStore(tmp_path / 'profiles.sqlite')
    paths = [tmp_path / f'{i}.ddd' for i in range(3)]
    for path in paths:
        path.write_bytes(path.name.encode())
    completed = []
    def loader(path):
        return [card([RawSegment(date(2026, 10, 1), 'drive', 0, 60)], path.stem)]
    result = import_files(store, paths, loader=loader, cancelled=lambda: bool(completed),
                          progress=lambda done, total: completed.append((done, total)))
    assert completed == [(1, 3)]
    assert result.cancelled and result.imported == 1 and not result.errors
    assert [p.card_number for p in store.list_profiles()] == ['0']


def test_shell_creates_secondary_modules_once_on_first_visit(app, tmp_path, monkeypatch):
    from spedi import shell
    from brudnopis_ot.ui import main_window
    from markus_dispatcher.data import db
    monkeypatch.setattr(db.config, 'DATA_DIR', tmp_path / 'dispatcher')
    monkeypatch.setattr(db.config, 'DB_PATH', tmp_path / 'dispatcher' / 'test.db')
    monkeypatch.setattr(db.config, 'ARCHIVE_ROOT', tmp_path / 'archive')
    monkeypatch.setattr(db.config, 'ROZPISKA_DB_CANDIDATE', tmp_path / 'none.db')
    monkeypatch.setenv('SPEDI_DATA_DIR', str(tmp_path / 'data'))
    store = DddProfileStore(tmp_path / 'data' / 'ddd_profiles.sqlite')
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    window = shell.ShellWindow()
    try:
        assert window._loaded_pages == {0}
        assert window.stack.count() == 3
        window._show_page(1)
        ewidentor = window.stack.widget(1)
        window._show_page(2)
        dispatcher = window.stack.widget(2)
        window._show_page(0)
        window._show_page(1)
        assert window.stack.widget(1) is ewidentor
        window._show_page(2)
        assert window.stack.widget(2) is dispatcher
        assert window._loaded_pages == {0, 1, 2}
    finally:
        window._mobile_timer.stop()
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('zone', ['UTC', 'PL'])
def test_night_analysis_limits_candidates_and_matches_full_scan(monkeypatch, zone):
    from ddd_analyzer import night_work
    from ddd_analyzer.models import ActivitySegment, ActivityType
    start = date(2026, 1, 1)
    segments = []
    for offset in range(365):
        day = start + timedelta(days=offset)
        segments.extend(ActivitySegment(day, ActivityType.WORK, i*15, (i+1)*15) for i in range(36))
        segments.append(ActivitySegment(day, ActivityType.REST, 540, 1440))
    full = night_work._work_segments(segments, zone)
    original = night_work._period_work
    candidates = []
    def checked(start, end, items, window):
        result = original(start, end, items, window)
        assert result == original(start, end, full, window)
        candidates.append(len(items))
        return result
    monkeypatch.setattr(night_work, '_period_work', checked)
    result = night_work.analyze_night_work(segments, zone=zone)
    assert len(result.periods) == 365
    assert sum(candidates) <= len(full) * 2


def test_geocoding_coalesces_requests_and_releases_threads(app, tmp_path, monkeypatch, wait_for):
    from PySide6.QtCore import QThread, QCoreApplication, QEvent
    from markus_dispatcher.data import db
    from markus_dispatcher.ui import geocode_worker
    from markus_dispatcher.ui.job_pane import JobPane
    from markus_dispatcher.core.models import Coordinates
    monkeypatch.setattr(db.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db.config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(db.config, 'ARCHIVE_ROOT', tmp_path / 'archive')
    monkeypatch.setattr(db.config, 'ROZPISKA_DB_CANDIDATE', tmp_path / 'none.db')
    db.init_db()
    entered, release = threading.Event(), threading.Event()
    requests = []
    def geocode(address):
        requests.append(address)
        if len(requests) == 1:
            entered.set()
            assert release.wait(5)
        return Coordinates(lat=1, lon=2)
    monkeypatch.setattr(geocode_worker, 'geocode_address', geocode)
    pane = JobPane()
    try:
        pane.load_address.setPlainText('First synthetic street')
        pane._request_geocode('load')
        wait_for(entered.is_set)
        for _ in range(10):
            pane._request_geocode('load')
        assert pane._pending_geocode['load'] is None
        for i in range(10):
            pane.load_address.setPlainText(f'Last synthetic street {i}')
            pane._request_geocode('load')
        assert len(pane.findChildren(QThread)) == 1
        release.set()
        wait_for(lambda: len(requests) == 2 and pane._load_thread is None)
        assert requests == ['First synthetic street', 'Last synthetic street 9']
        assert pane._load_coords == Coordinates(lat=1, lon=2)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not pane.findChildren(QThread)
    finally:
        release.set()
        wait_for(lambda: pane._load_thread is None)
        pane.close()
        pane.deleteLater()


def test_scenario_copy_keeps_edits_independent():
    from brudnopis_ot.core.models import ActivityKind, Block, Scenario, copy_scenario
    start = datetime(2026, 10, 5)
    original = Scenario(week_start=start.date(), blocks=[Block(ActivityKind.DRIVE, start, start+timedelta(hours=1))])
    cloned = copy_scenario(original)
    assert cloned == original and cloned is not original
    cloned.blocks[0].label = 'Changed'
    cloned.blocks[0].kind = ActivityKind.WORK
    cloned.blocks.append(Block(ActivityKind.REST, start+timedelta(hours=1), start+timedelta(hours=12)))
    assert len(original.blocks) == 1
    assert original.blocks[0].kind == ActivityKind.DRIVE and original.blocks[0].label is None


def test_datetime_serialization_preserves_legacy_format():
    from datetime import timezone
    from brudnopis_ot.core.serialize import _dt_to_str
    for year in (12, 999, 1000, 2026, 9999):
        for zone in (None, timezone.utc, timezone(timedelta(hours=5, minutes=30))):
            value = datetime(year, 10, 25, 2, 30, 59, 123456, tzinfo=zone, fold=1)
            assert _dt_to_str(value) == value.strftime('%Y-%m-%dT%H:%M:%S')


def test_dispatcher_month_filter_preserves_legacy_date_formats(tmp_path, monkeypatch):
    from markus_dispatcher.data import db, monthly_settlements as months
    monkeypatch.setattr(db.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db.config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(db.config, 'ARCHIVE_ROOT', tmp_path / 'archive')
    monkeypatch.setattr(db.config, 'ROZPISKA_DB_CANDIDATE', tmp_path / 'none.db')
    db.init_db()
    driver = db.add_driver('Synthetic')
    dates = ['01.10.2026 08:00', '1.10.2026 8:00', ' 1.10.2026 8:00', '1.10.2026  8:00',
             '01.10.2026\t08:00', '01.09.2026 08:00', '1.9.2026 8:00', '01.10.2025 08:00',
             '31.02.2026 08:00', '01.13.2026 08:00', '', 'BAD', '01.01.2026 08:00']
    with db.get_conn() as conn:
        conn.executemany('INSERT INTO jobs (driver_id, load_datetime, unload_datetime) VALUES (?,?,?)',
                         [(driver.id, '01.01.2026 08:00', value) for value in dates])
    jobs = db.list_jobs_for_driver(driver.id)
    for month in ('2026-01', '2026-09', '2026-10', '2025-10', 'BAD'):
        expected = [job.id for job in jobs if months.month_of(job.unload_datetime) == month]
        assert [job.id for job in months.jobs_for_month(driver.id, month)] == expected


def test_rebase_preserves_splits_and_overlapping_corrections():
    from brudnopis_ot.core.models import ActivityKind, Block, Scenario
    from brudnopis_ot.core.profile_scenario import rebase_scenario
    start = datetime(2026, 10, 5)
    baseline = Scenario(week_start=start.date(), blocks=[
        Block(ActivityKind.DRIVE, start+timedelta(hours=2), start+timedelta(hours=4), id='a'),
        Block(ActivityKind.DRIVE, start+timedelta(hours=3), start+timedelta(hours=6), id='b'),
        Block(ActivityKind.DRIVE, start+timedelta(hours=8), start+timedelta(hours=10), id='c')])
    edited = replace(baseline, blocks=[replace(baseline.blocks[2], kind=ActivityKind.WORK)])
    current = replace(baseline, blocks=[Block(ActivityKind.DRIVE, start, start+timedelta(hours=12), id='new')])
    result = rebase_scenario(baseline, edited, current)
    assert [(b.kind, (b.start-start).total_seconds()/3600, (b.end-start).total_seconds()/3600) for b in result.blocks] == [
        (ActivityKind.DRIVE, 0, 2), (ActivityKind.DRIVE, 6, 8),
        (ActivityKind.WORK, 8, 10), (ActivityKind.DRIVE, 10, 12)]


def test_large_profile_save_uses_worker_connection_and_preserves_edits(app, tmp_path, monkeypatch):
    from brudnopis_ot.ui import main_window
    from brudnopis_ot.core.models import ActivityKind, copy_scenario
    from PySide6.QtCore import QTimer
    saved_threads = []
    class TrackingStore(DddProfileStore):
        def save_ddd_scenario(self, key, scenario):
            saved_threads.append(threading.get_ident())
            return super().save_ddd_scenario(key, scenario)
    store = TrackingStore(tmp_path / 'profiles.sqlite')
    day = date(2026, 8, 3)
    profile = store.upsert(card([RawSegment(day+timedelta(days=i//96), 'rest' if i%2 else 'drive',
                                          i%96*15, (i%96+1)*15) for i in range(5002)]))
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    window = main_window.MainWindow()
    try:
        window._ddd_profile = profile
        window._ddd_source = store.load_ddd_scenario(profile.profile_key)
        window._scenario = copy_scenario(window._ddd_source)
        window._ddd_view_blocks = {b.id: replace(b) for b in window._scenario.blocks}
        window._scenario.blocks[0].kind = ActivityKind.WORK
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        assert window._persist_ddd()
        assert ticks and saved_threads and saved_threads != [threading.get_ident()]
        assert store.load_ddd_scenario(profile.profile_key).blocks[0].kind == ActivityKind.WORK
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_closed_document_importers_are_deleted_by_caller(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from markus_dispatcher.data import db
    from markus_dispatcher.ui.document_import_dialog import DocumentImportDialog
    from markus_dispatcher.ui.job_pane import JobPane
    monkeypatch.setattr(db.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db.config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(db.config, 'ARCHIVE_ROOT', tmp_path / 'archive')
    monkeypatch.setattr(db.config, 'ROZPISKA_DB_CANDIDATE', tmp_path / 'none.db')
    monkeypatch.setenv('TEMP', str(tmp_path))
    monkeypatch.setenv('TMP', str(tmp_path))
    db.init_db()
    pane = JobPane()
    pane._driver_id = db.add_driver('Synthetic').id
    def cancel(dialog):
        dialog.reject()
        return DocumentImportDialog.DialogCode.Rejected
    monkeypatch.setattr(DocumentImportDialog, 'exec', cancel)
    for _ in range(20):
        pane._import_document()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not pane.findChildren(DocumentImportDialog)
    pane.close()
    pane.deleteLater()


def test_confirmed_segments_can_clip_long_supplements_to_month():
    from brudnopis_ot.core.activity_ends import confirmed_segments
    rows = [{'start': '2020-01-01T12:00:00', 'end': '2030-01-01T12:00:00', 'activity': 'rest'}]
    actual = confirmed_segments([], rows, range_start=datetime(2026, 10, 1), range_end=datetime(2026, 11, 1))
    assert len(actual) == 31
    assert actual[0] == RawSegment(date(2026, 10, 1), 'rest', 0, 1440)
    assert actual[-1] == RawSegment(date(2026, 10, 31), 'rest', 0, 1440)


def test_timeline_cache_distinguishes_ambiguous_aware_instants(app):
    from zoneinfo import ZoneInfo
    from brudnopis_ot.ui.week_grid import WeekGridWidget
    grid = WeekGridWidget()
    grid.set_display_zone('UTC')
    first = datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo('Europe/Warsaw'), fold=0)
    second = first.replace(fold=1)
    assert grid._disp(first) == datetime(2026, 10, 25, 0, 30)
    assert grid._disp(second) == datetime(2026, 10, 25, 1, 30)
    grid.close()


def test_shell_waits_for_background_work_without_blocking_event_loop(app, tmp_path, monkeypatch, wait_for):
    from PySide6.QtCore import QThread, QTimer
    from spedi import shell
    from brudnopis_ot.ui import main_window
    store = DddProfileStore(tmp_path / 'profiles.sqlite')
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    window = shell.ShellWindow()
    released = threading.Event()
    entered = threading.Event()
    class Task(QThread):
        def run(self):
            entered.set()
            assert released.wait(5)
    task = Task(window)
    window.show()
    task.start()
    try:
        wait_for(entered.is_set)
        assert not window.close()
        assert window.isVisible() and window._closing_requested
        ticks = []
        QTimer.singleShot(0, lambda: (ticks.append(True), released.set()))
        wait_for(lambda: not window.isVisible())
        assert ticks and not task.isRunning()
    finally:
        released.set()
        assert task.wait(5000)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_interval_index_handles_nested_ranges_boundaries_and_original_order():
    from brudnopis_ot.core.interval_index import IntervalIndex
    items = [(8, 9, 'late'), (0, 100, 'long'), (2, 3, 'early'), (5, 7, 'middle')]
    index = IntervalIndex(items, lambda item: item[:2])
    assert index.overlapping(3, 5, original_order=True) == [items[1]]
    assert index.overlapping(6, 9, original_order=True) == [items[0], items[1], items[3]]
    assert index.overlapping(100, 101) == []
    assert index.overlapping(5, 5) == []


def test_background_failure_is_returned_to_caller_and_dialog_is_retired(app):
    from PySide6.QtCore import QCoreApplication, QEvent
    from spedi.background import run_background, OperationDialog
    def fail(task):
        raise ValueError('synthetic failure')
    with pytest.raises(ValueError, match='synthetic failure'):
        run_background(None, 'Synthetic', fail)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not any(isinstance(widget, OperationDialog) for widget in app.topLevelWidgets())


def test_timeline_lens_does_not_keep_its_qt_parent_alive(app):
    import gc
    import weakref
    from PySide6.QtCore import QCoreApplication, QEvent
    from brudnopis_ot.ui.timeline_lens import TimelineLensDialog
    from brudnopis_ot.ui.week_grid import WeekGridWidget
    from brudnopis_ot.core.models import Scenario
    grid = WeekGridWidget()
    grid.set_scenario(Scenario(week_start=date(2026, 10, 5)))
    reference = weakref.ref(grid)
    dialog = TimelineLensDialog(grid, [(datetime(2026, 10, 5), datetime(2026, 10, 6))])
    dialog.deleteLater()
    del grid
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    assert reference() is None


def test_repeated_ocr_releases_completed_tasks_and_crop_images(app, tmp_path, monkeypatch, wait_for):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QImage
    from markus_dispatcher.ui import document_import_dialog as ui
    monkeypatch.setenv('TEMP', str(tmp_path))
    monkeypatch.setenv('TMP', str(tmp_path))
    monkeypatch.setattr(ui.readers, 'tesseract_path', lambda: 'synthetic-engine')
    monkeypatch.setattr(ui.readers, 'run_local_ocr', lambda image: 'Synthetic OCR')
    dialog = ui.DocumentImportDialog()
    try:
        for _ in range(5):
            image = QImage(1000, 1000, QImage.Format.Format_RGB32)
            image.fill(0xffffffff)
            dialog._crop = image
            dialog._run_ocr()
            wait_for(lambda: dialog._ocr is None)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert not dialog.findChildren(ui.OcrTask)
            assert dialog.selection.toPlainText() == 'Synthetic OCR'
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_failed_profile_switch_keeps_previous_identity_and_edits(app, tmp_path, monkeypatch):
    from brudnopis_ot.ui import main_window
    store = DddProfileStore(tmp_path / 'profiles.sqlite')
    old = store.create_template('Previous', date(2026, 10, 5))
    new = store.upsert(card([RawSegment(date(2026, 10, 1), 'drive', 0, 60)]))
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    monkeypatch.setattr(main_window.QMessageBox, 'warning', lambda *args: None)
    window = main_window.MainWindow()
    try:
        window._apply_ddd_profile(old)
        scenario, source, session = window._scenario, window._template_source, window._ddd_session
        window._dirty = True
        def fail(*args, **kwargs):
            raise OSError('Synthetic database failure')
        monkeypatch.setattr(store, 'load_ddd_scenario', fail)
        with pytest.raises(OSError, match='Synthetic database failure'):
            window._apply_ddd_profile(new)
        assert window._ddd_profile.profile_key == old.profile_key
        assert window._scenario is scenario and window._template_source is source
        assert window._ddd_session is session and window._dirty
        window._dirty = False
        window._on_ddd_profile_selected(window._ddd_combo.findData(new.profile_key))
        assert window._ddd_combo.currentData() == old.profile_key
        assert window._ddd_profile.profile_key == old.profile_key
    finally:
        window._dirty = False
        window.close()
        window.deleteLater()
