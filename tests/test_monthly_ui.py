from datetime import date, datetime, timedelta
import os

import pytest

from markus_dispatcher.data import db, monthly_settlements as months, settlement_store
from markus_dispatcher.core.models import JobData


@pytest.fixture
def local_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db.config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(db.config, 'ARCHIVE_ROOT', tmp_path / 'archive')
    monkeypatch.setattr(db.config, 'ROZPISKA_DB_CANDIDATE', tmp_path / 'none.db')
    db.init_db()
    return db.add_driver('Kierowca testowy')


@pytest.fixture
def app():
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication([])
    return application


def make_job(driver, unload, load='31.08.2026 12:00', status='done'):
    return db.save_job(JobData(driver_id=driver.id, unload_datetime=unload, load_datetime=load, status=status))


def test_month_uses_unload_date_and_year_and_keeps_all_records(local_db):
    september = make_job(local_db, '01.09.2026 08:00')
    october = make_job(local_db, '01.10.2026 08:00', load='30.09.2026 15:00')
    year = make_job(local_db, '01.09.2025 08:00')
    missing = make_job(local_db, '')
    assert [j.id for j in months.jobs_for_month(local_db.id, '2026-09')] == [september.id]
    assert [j.id for j in months.jobs_for_month(local_db.id, '2026-10')] == [october.id]
    assert db.get_job(year.id) and db.get_job(missing.id)


def test_monthly_file_and_driver_tabs_persist_independently(local_db):
    other = db.add_driver('Inny kierowca')
    tabs = [{'sheetId': 0, 'title': 'Pierwszy'}, {'sheetId': 7, 'title': 'Drugi'}]
    months.assign('2026-09', 'sept_file', tabs)
    months.bind_driver('2026-09', local_db.id, 'sept_file', 0)
    months.bind_driver('2026-09', other.id, 'sept_file', 7)
    months.assign('2026-10', 'oct_file', tabs)
    assert months.get('2026-09')['drivers'] == {str(local_db.id): 0, str(other.id): 7}
    assert months.get('2026-10')['drivers'] == {}
    months.assign('2026-09', 'replacement', tabs)
    assert months.get('2026-09')['drivers'] == {}
    assert months.get('2026-10')['file_id'] == 'oct_file'
    with pytest.raises(ValueError):
        months.bind_driver('2026-09', local_db.id, 'sept_file', 0)


def test_history_month_switch_saves_edits_and_hides_other_month(app, local_db):
    from PySide6.QtCore import QDate
    from markus_dispatcher.ui.job_pane import JobPane
    september = make_job(local_db, '02.09.2026 09:00')
    october = make_job(local_db, '02.10.2026 09:00')
    pane = JobPane()
    pane.month_edit.setDate(QDate(2026, 9, 1))
    pane.bind_driver(local_db.id)
    assert pane._job_id == september.id
    assert pane.history_combo.findData(october.id) == -1
    pane.ref_edit.setText('saved before month switch')
    pane.settlement.fields['price'].setText('500')
    pane.month_edit.setDate(QDate(2026, 10, 1))
    assert pane._job_id == october.id
    assert pane.history_combo.findData(september.id) == -1
    assert db.get_job(september.id).ref_numbers == 'saved before month switch'
    assert settlement_store.load(september.id)['draft']['price'] == '500'
    pane.month_edit.setDate(QDate(2026, 9, 1))
    assert pane._job_id == september.id
    pane.close()


def test_cached_month_target_reused_without_network_and_without_other_driver_tab(app, local_db, monkeypatch):
    from PySide6.QtCore import QDate
    from markus_dispatcher.ui.job_pane import JobPane
    from markus_dispatcher.core.google_sheets import Sheets
    def fail(*args, **kwargs):
        pytest.fail('Local monthly target must not request Google or open a browser')
    monkeypatch.setattr(Sheets, 'tabs', fail)
    tabs = [{'sheetId': 0, 'title': 'Driver one tab'}, {'sheetId': 1, 'title': 'Driver two tab'}]
    months.assign('2026-09', 'monthly', tabs)
    months.bind_driver('2026-09', local_db.id, 'monthly', 0)
    pane = JobPane()
    pane.month_edit.setDate(QDate(2026, 9, 1))
    pane.new_job(driver_id=local_db.id)
    assert pane.settlement.file_id == 'monthly'
    assert pane.settlement.tabs.currentData() == 0
    pane.new_job(driver_id=local_db.id)
    assert pane.settlement.tabs.currentData() == 0
    other = db.add_driver('Other')
    pane.new_job(driver_id=other.id)
    assert pane.settlement.file_id == 'monthly'
    assert pane.settlement.tabs.currentData() is None
    pane.settlement.tabs.setCurrentIndex(pane.settlement.tabs.findData(1))
    pane.new_job(driver_id=other.id)
    assert pane.settlement.tabs.currentData() == 1
    pane.month_edit.setDate(QDate(2026, 10, 1))
    assert pane.settlement.file_id == ''
    pane.close()


def test_sent_binding_survives_month_default_replacement(app, local_db):
    from markus_dispatcher.ui.job_pane import JobPane
    job = make_job(local_db, '02.09.2026 09:00')
    binding = {'file_id': 'original', 'sheet_id': 9, 'title': 'Original'}
    settlement_store.save_binding(job.id, binding)
    months.assign('2026-09', 'replacement', [{'sheetId': 1, 'title': local_db.display_name}])
    pane = JobPane()
    pane.load_job(job)
    pane.settlement.apply_month_target()
    assert pane.settlement.file_id == 'original'
    assert pane.settlement.tabs.currentData() == 9
    assert settlement_store.load(job.id)['binding'] == binding
    pane.close()


def test_lens_range_clips_and_supports_reverse_drag_and_multiple_weeks(app):
    from PySide6.QtCore import QRectF
    from brudnopis_ot.core.models import Scenario
    from brudnopis_ot.ui.week_grid import WeekGridWidget
    from brudnopis_ot.ui.timeline_lens import selected_ranges
    grid = WeekGridWidget()
    grid.set_scenario(Scenario(week_start=date(2026, 9, 7), weeks=2))
    px = grid._px_per_hour_effective()
    rectangle = QRectF(grid._label_width + 12 * px, grid._header_height + 150, -2 * px, -145)
    ranges = selected_ranges(grid, rectangle)
    assert ranges[0] == (datetime(2026, 9, 7, 10), datetime(2026, 9, 7, 12))
    assert ranges[1] == (datetime(2026, 9, 14, 10), datetime(2026, 9, 14, 12))
    assert selected_ranges(grid, QRectF(0, 0, 50, 20)) == []
    grid.close()


def test_lens_click_identifies_tiny_block_and_does_not_change_axis_zoom(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from brudnopis_ot.core.models import Scenario, Block, ActivityKind, ACTIVITY_COLORS
    from brudnopis_ot.ui.week_grid import WeekGridWidget
    from brudnopis_ot.ui.timeline_lens import TimelineLensDialog
    start = datetime(2026, 9, 7, 10)
    block = Block(ActivityKind.AVAILABILITY, start, start + timedelta(minutes=1))
    grid = WeekGridWidget()
    grid.set_display_zone('UTC')
    grid.set_scenario(Scenario(week_start=date(2026, 9, 7), weeks=1, blocks=[block]))
    original_zoom = grid._hours_per_day_px
    dialog = TimelineLensDialog(grid, [(start - timedelta(minutes=5), start + timedelta(minutes=5))])
    dialog.show()
    app.processEvents()
    assert ACTIVITY_COLORS[ActivityKind.AVAILABILITY] == '#1cfc03'
    identity, rectangle, _ = dialog.detail.hits[0]
    assert identity == block.id and rectangle.width() > 20
    QTest.mouseClick(dialog.detail, Qt.MouseButton.LeftButton, pos=rectangle.center().toPoint())
    assert dialog.blocks.currentItem().data(Qt.ItemDataRole.UserRole) == block.id
    assert dialog.selected_id is None
    dialog.choose_current()
    assert dialog.selected_id == block.id
    assert grid._hours_per_day_px == original_zoom
    assert block.duration == timedelta(minutes=1)
    grid.set_lens_mode(True)
    QTest.keyClick(grid, Qt.Key.Key_Escape)
    assert not grid._lens_mode
    grid.close()


def test_lens_drag_emits_block_selection_without_empty_click(app, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from brudnopis_ot.core.models import Scenario, Block, ActivityKind
    from brudnopis_ot.ui.week_grid import WeekGridWidget
    from brudnopis_ot.ui import timeline_lens
    block = Block(ActivityKind.WORK, datetime(2026, 9, 7, 10), datetime(2026, 9, 7, 11))
    grid = WeekGridWidget()
    grid.set_scenario(Scenario(week_start=date(2026, 9, 7), weeks=1, blocks=[block]))
    grid.show()
    events = []
    grid.block_clicked.connect(lambda identity: events.append(identity))
    grid.empty_clicked.connect(lambda moment: events.append('empty'))
    def choose(dialog):
        dialog.selected_id = block.id
        return 1
    monkeypatch.setattr(timeline_lens.TimelineLensDialog, 'exec', choose)
    grid.set_lens_mode(True)
    QTest.mousePress(grid, Qt.MouseButton.LeftButton, pos=QPoint(170, 40))
    QTest.mouseMove(grid, QPoint(260, 75))
    QTest.mouseRelease(grid, Qt.MouseButton.LeftButton, pos=QPoint(260, 75))
    assert events == [block.id]
    assert not grid._lens_mode and not grid._lens_band.isVisible()
    grid.close()
