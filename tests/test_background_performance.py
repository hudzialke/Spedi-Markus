"""Concurrency, cache invalidation and column correctness with synthetic inputs."""
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
import threading

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.setenv('SPEDI_LANGUAGE', 'pl')
    monkeypatch.setenv('TEMP', str(tmp_path))
    monkeypatch.setenv('TMP', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def make_pdf(path, label='DEMO'):
    from reportlab.pdfgen.canvas import Canvas
    canvas = Canvas(str(path), pagesize=(600, 800))
    for x, y, text in ((40, 700, label+' LOAD'), (340, 700, label+' UNLOAD'),
                       (40, 680, 'Street 12/3 (B)'), (340, 680, 'Other Road 45')):
        canvas.drawString(x, y, text)
    canvas.showPage()
    canvas.drawString(40, 700, label+' SECOND PAGE')
    canvas.save()
    return path


def test_prepared_pdf_selection_never_calls_pdf_again(app, tmp_path):
    from PySide6.QtCore import QRectF
    from PySide6.QtPdf import QPdfDocument
    from markus_dispatcher.ui.document_import_dialog import index_pdf_page, pdf_text_in_rect
    document = QPdfDocument()
    document.load(str(make_pdf(tmp_path/'synthetic.pdf')))
    prepared = index_pdf_page(document, 0)
    document.close()
    assert pdf_text_in_rect(None, 0, QRectF(30, 80, 230, 80), prepared) == 'DEMO LOAD\nStreet 12/3 (B)'
    assert pdf_text_in_rect(None, 0, QRectF(330, 80, 230, 80), prepared) == 'DEMO UNLOAD\nOther Road 45'


def test_pdf_page_cache_and_document_replacement(app, tmp_path, monkeypatch, wait_for):
    from markus_dispatcher.ui import document_import_dialog as ui
    from PySide6.QtCore import QRectF
    calls = []
    original = ui.index_pdf_page
    def counted(document, page, cancelled):
        calls.append(page)
        return original(document, page, cancelled)
    monkeypatch.setattr(ui, 'index_pdf_page', counted)
    monkeypatch.setattr(ui.DocumentImportDialog, '_can_replace_document', lambda _: True)
    dialog = ui.DocumentImportDialog()
    dialog.open_path(make_pdf(tmp_path/'first.pdf', 'FIRST'))
    wait_for(lambda: dialog._pdf_worker is None)
    first_index = dialog._page_index
    dialog.page_combo.setCurrentIndex(1)
    wait_for(lambda: dialog._pdf_worker is None)
    dialog.page_combo.setCurrentIndex(0)
    assert dialog._page_index is first_index
    assert calls == [0, 1]
    dialog.open_path(make_pdf(tmp_path/'second.pdf', 'SECOND'))
    wait_for(lambda: dialog._pdf_worker is None)
    dialog._region(QRectF(30/600, 80/800, 230/600, 80/800))
    assert 'SECOND LOAD' in dialog.selection.toPlainText()
    dialog._pdf_indexed(dialog._pdf_generation-1, first_index)
    assert dialog._page_index is not first_index
    dialog.reject()


def test_pdf_background_cancel_and_manual_correction(app, tmp_path, monkeypatch, wait_for):
    from markus_dispatcher.ui import document_import_dialog as ui
    from PySide6.QtCore import QRectF, QTimer
    entered, release = threading.Event(), threading.Event()
    original = ui.index_pdf_page
    def delayed(document, page, cancelled):
        entered.set()
        while not release.wait(.01):
            if cancelled():
                return None
        return original(document, page, cancelled)
    monkeypatch.setattr(ui, 'index_pdf_page', delayed)
    dialog = ui.DocumentImportDialog()
    dialog.show()
    dialog.open_path(make_pdf(tmp_path/'synthetic.pdf'))
    wait_for(entered.is_set)
    wait_for(lambda: not dialog.canvas.image.isNull())
    dialog._region(QRectF(30/600, 80/800, 230/600, 80/800))
    assert dialog._pending_region is not None
    dialog.selection.setPlainText('MANUAL CORRECTION')
    ticks = []
    QTimer.singleShot(0, lambda: ticks.append(True))
    wait_for(lambda: bool(ticks))
    release.set()
    wait_for(lambda: dialog._pdf_worker is None)
    assert dialog.selection.toPlainText() == 'MANUAL CORRECTION'
    entered.clear()
    release.clear()
    dialog.page_combo.setCurrentIndex(1)
    wait_for(entered.is_set)
    dialog.reject()
    wait_for(lambda: dialog._pdf_worker is None)
    assert not dialog.isVisible()


def test_pdf_rapid_page_change_drops_obsolete_work(app, tmp_path, monkeypatch, wait_for):
    from markus_dispatcher.ui import document_import_dialog as ui
    from PySide6.QtCore import QRectF
    entered = threading.Event()
    original = ui.index_pdf_page
    def delayed(document, page, cancelled):
        if page == 0:
            entered.set()
            while not cancelled():
                threading.Event().wait(.005)
            return None
        return original(document, page, cancelled)
    monkeypatch.setattr(ui, 'index_pdf_page', delayed)
    dialog = ui.DocumentImportDialog()
    dialog.open_path(make_pdf(tmp_path/'synthetic.pdf'))
    wait_for(entered.is_set)
    dialog.page_combo.setCurrentIndex(1)
    wait_for(lambda: dialog._pdf_worker is None)
    dialog._region(QRectF(30/600, 80/800, 330/600, 40/800))
    assert dialog.selection.toPlainText() == 'DEMO SECOND PAGE'
    assert set(dialog._page_cache) == {1}
    dialog.reject()


@pytest.fixture
def watch_inputs(tmp_path):
    from brudnopis_ot.core.ddd_store import DddProfileStore
    from brudnopis_ot.core.models import Scenario, Block, ActivityKind
    from spedi.weekly_watch import WatchStore, WatchOptions
    store = DddProfileStore(tmp_path/'synthetic.sqlite')
    start = datetime(2026, 8, 7, 12)
    scenario = Scenario(week_start=date(2026, 8, 3), title='Synthetic',
        blocks=[Block(ActivityKind.REST, start+timedelta(weeks=i), start+timedelta(weeks=i, hours=hours),
                      country_start='DE') for i, hours in enumerate((45, 24, 45))])
    first = store.create_template('Synthetic A', scenario.week_start)
    second = store.create_template('Synthetic B', scenario.week_start)
    store.save_template_scenario(first.profile_key, replace(scenario, title='Synthetic A'))
    store.save_template_scenario(second.profile_key, replace(scenario, title='Synthetic B'))
    settings = WatchStore(store.path)
    settings.save(first.profile_key, WatchOptions(selected=True))
    return store, scenario, first, second, settings


def execute_task(store, cache, only_selected=True, search=''):
    from spedi.weekly_watch_tasks import WatchTask
    results, failed, invalidated = {}, [], []
    task = WatchTask(store.path, 1, only_selected, search, cache=cache)
    task.result_ready.connect(lambda _, key, result: results.update({key: result[0]}))
    task.failed.connect(lambda *args: failed.append(args))
    task.row_failed.connect(lambda *args: failed.append(args))
    task.invalidated.connect(lambda *args: invalidated.append(args))
    task.run()
    assert not failed
    return results, invalidated


def test_watch_skips_hidden_profiles_and_reuses_results(app, watch_inputs, monkeypatch):
    from spedi import weekly_watch_tasks as tasks
    store, scenario, first, second, settings = watch_inputs
    cache = tasks.WatchResultCache()
    calls = []
    original = tasks.calculate
    monkeypatch.setattr(tasks, 'calculate', lambda *a: (calls.append(True), original(*a))[1])
    results, invalidated = execute_task(store, cache)
    assert set(results) == {first.profile_key} and len(calls) == 1 and not invalidated
    assert execute_task(store, cache)[0] == results
    assert len(calls) == 1
    all_results, _ = execute_task(store, cache, only_selected=False)
    assert set(all_results) == {first.profile_key, second.profile_key} and len(calls) == 2
    assert set(execute_task(store, cache, only_selected=False, search='synthetic b')[0]) == {second.profile_key}


@pytest.mark.parametrize('change', ['anchor', 'week_start', 'country', 'plan', 'options', 'issues'])
def test_watch_invalidates_every_relevant_input(app, watch_inputs, monkeypatch, change):
    from spedi import weekly_watch_tasks as tasks
    from brudnopis_ot.core.models import Block, ActivityKind
    store, scenario, first, second, settings = watch_inputs
    cache = tasks.WatchResultCache()
    calls = []
    original = tasks.calculate
    monkeypatch.setattr(tasks, 'calculate', lambda *a: (calls.append(True), original(*a))[1])
    execute_task(store, cache)
    if change == 'anchor':
        scenario.anchor_block_id = scenario.blocks[1].id
    elif change == 'week_start':
        scenario.week_start += timedelta(weeks=1)
    elif change == 'country':
        scenario.employer_country = 'DE'
    elif change == 'plan':
        start = scenario.blocks[-1].end
        scenario.blocks.append(Block(ActivityKind.WORK, start, start+timedelta(hours=2)))
    elif change == 'options':
        settings.save(first.profile_key, replace(settings.get(first.profile_key), residence_country='IT'))
    elif change == 'issues':
        store.add_data_issue(first.profile_key, scenario.blocks[-1].start, scenario.blocks[-1].end, 'synthetic issue')
    store.save_template_scenario(first.profile_key, scenario)
    results, _ = execute_task(store, cache)
    assert len(calls) == 2
    if change == 'issues':
        assert results[first.profile_key].reduced == 'unknown'
        assert results[first.profile_key].return_by is None


def test_watch_drops_result_if_database_changes_during_calculation(app, watch_inputs, monkeypatch):
    from spedi import weekly_watch_tasks as tasks
    store, scenario, first, second, settings = watch_inputs
    original = tasks.calculate
    def changed(*args):
        settings.save(first.profile_key, replace(settings.get(first.profile_key), residence_country='FR'))
        return original(*args)
    monkeypatch.setattr(tasks, 'calculate', changed)
    results, invalidated = execute_task(store, tasks.WatchResultCache())
    assert not results and invalidated


def test_watch_ui_remains_responsive_and_cancels(app, watch_inputs, monkeypatch, wait_for):
    from spedi import weekly_watch_tasks as tasks
    from spedi.weekly_watch_ui import WeeklyWatchDialog
    from PySide6.QtCore import QTimer
    entered, release = threading.Event(), threading.Event()
    original = tasks.calculate
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(tasks, 'calculate', delayed)
    store, *_ = watch_inputs
    dialog = WeeklyWatchDialog(store)
    dialog.show()
    try:
        wait_for(entered.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        wait_for(lambda: bool(ticks))
        dialog._cancel_calculation()
        release.set()
        wait_for(lambda: dialog._worker is None)
        assert not dialog.results
        assert 'anulowane' in dialog.progress.text()
        dialog.reload()
        wait_for(lambda: dialog._worker is None and not dialog._pending)
        assert len(dialog.results) == 1
    finally:
        release.set()
        dialog.reject()


def test_watch_cache_is_bounded():
    from spedi.weekly_watch_tasks import WatchResultCache
    cache = WatchResultCache(capacity=2)
    for key in range(3):
        cache.put(key, key+1)
    assert len(cache.entries) == 2 and cache.get(0) is None


def test_watch_cache_invalidates_ddd_import_and_corrections(app, tmp_path, monkeypatch):
    from spedi import weekly_watch_tasks as tasks
    from spedi.weekly_watch import WatchStore, WatchOptions
    from brudnopis_ot.core.ddd_store import DddProfileStore
    from brudnopis_ot.core.ddd_file_loader import DddLoadResult
    from brudnopis_ot.core.hudonizator_reader import RawSegment
    from brudnopis_ot.core.models import ActivityKind
    store = DddProfileStore(tmp_path/'synthetic-ddd.sqlite')
    day = date(2026, 8, 3)
    profile = store.upsert(DddLoadResult([RawSegment(day, 'drive', 60, 120)],
        'Synthetic DDD', 'Synthetic DDD', 'SYNTHETIC-CARD', None, 'card', (), day, day))
    WatchStore(store.path).save(profile.profile_key, WatchOptions(selected=True))
    cache, calls = tasks.WatchResultCache(), []
    original = tasks.calculate
    monkeypatch.setattr(tasks, 'calculate', lambda *a: (calls.append(True), original(*a))[1])
    execute_task(store, cache)
    execute_task(store, cache)
    assert len(calls) == 1
    edited = store.load_ddd_scenario(profile.profile_key)
    edited.blocks[0].kind = ActivityKind.WORK
    store.save_ddd_scenario(profile.profile_key, edited)
    execute_task(store, cache)
    assert len(calls) == 2
    profile.segments.append(RawSegment(day+timedelta(days=1), 'drive', 60, 120))
    store.upsert(profile)
    execute_task(store, cache)
    assert len(calls) == 3
