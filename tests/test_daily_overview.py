from datetime import date, datetime, timezone

from brudnopis_ot.core.ddd_store import DddProfileStore
from spedi.weekly_watch import WatchStore, WatchOptions, history_revision
from spedi.daily_overview import collect_actions, urgency
from tests.test_compensation_assignment import crossing_history
from tests.test_pod_tracking import pod


def test_daily_panel_combines_selected_driver_and_pod_actions_without_writes(tmp_path, pod):
    pod_store, job, ids = pod
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    profile = store.create_template('Synthetic driver', date(2026, 10, 5))
    scenario = crossing_history()
    store.save_template_scenario(profile.profile_key, scenario)
    WatchStore(store.path).save(profile.profile_key, WatchOptions(selected=True, verified_revision=history_revision(scenario)))
    pod_store.save(job.id, deadline=datetime(2026, 10, 29, 10, tzinfo=timezone.utc), required_docs='CMR,WZ', documents={ids[0]: 'CMR'})
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    items, errors = collect_actions(store.path, pod_store.path, pod_store.archive_root, now=now)
    assert not errors
    document = next(item for item in items if item.job_id == job.id)
    assert 'WZ' in document.title and urgency(document, now)[1] == 'Po terminie'
    home = next(item for item in items if item.identity.endswith('-home'))
    assert home.deadline == datetime(2026, 10, 31, 15, 33)
    assert pod_store.get(job.id)['sent_at'] is None


def test_empty_scope_is_explicit_and_unsent_requirements_do_not_invent_a_deadline(tmp_path, pod):
    pod_store, job, ids = pod
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    items, warnings = collect_actions(store.path, pod_store.path, pod_store.archive_root,
                                     now=datetime(2026, 10, 29, 12, tzinfo=timezone.utc))
    assert any(item.target == 'watchlist' for item in items)
    task = next(item for item in items if item.job_id == job.id)
    assert task.deadline is None and task.target == 'pod'


def test_approved_split_view_filters_both_lists_and_keeps_selection_consistent(tmp_path, monkeypatch, wait_for):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from spedi.daily_ui import DailyOverviewDialog
    from spedi.daily_overview import ActionItem
    app = QApplication.instance() or QApplication([])
    items = (ActionItem('driver', 'drivers', 'DEMO-A', 'Powrót', 'Synthetic driver detail', profile_key='demo'),
             ActionItem('pod', 'documents', 'DEMO/1', 'Brakuje CMR', 'Synthetic POD detail', job_id=1, target='pod'))
    dialog = DailyOverviewDialog('unused', 'unused', 'unused', loader=lambda *a, **k: (items, ()))
    dialog.show()
    try:
        wait_for(lambda: dialog._loaded and dialog._worker is None)
        assert dialog.models['drivers'].rowCount() == dialog.models['documents'].rowCount() == 1
        dialog.tables['drivers'].selectRow(0)
        assert dialog._current.identity == 'driver'
        dialog.tables['documents'].selectRow(0)
        assert dialog._current.identity == 'pod'
        assert not dialog.tables['drivers'].selectionModel().selectedRows()
        dialog.search.setText('CMR')
        assert dialog.proxies['drivers'].rowCount() == 0 and dialog.proxies['documents'].rowCount() == 1
        assert not dialog.open_button.isEnabled()
        dialog.tables['documents'].selectRow(0)
        dialog._open()
        assert dialog.chosen.identity == 'pod'
    finally:
        dialog.reject()
        dialog.deleteLater()
