import threading

import pytest

from tests.test_backups import manager


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_backup_dialog_schedules_without_changing_open_data(app, manager, monkeypatch):
    from spedi import backup_ui
    from tests.test_backups import change, assert_value
    archive = manager.create()
    change(manager, 'current')
    monkeypatch.setattr(backup_ui.QMessageBox, 'question', lambda *a, **k: backup_ui.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(backup_ui, 'run_background', lambda parent, label, action, **kw: action(None))
    dialog = backup_ui.BackupDialog(manager)
    try:
        assert dialog.list.count() == 1
        dialog.restore_selected()
        assert manager.pending.exists()
        assert_value(manager, 'current')
        dialog.cancel_restore()
        assert not manager.pending.exists()
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_history_dialog_calls_only_the_selected_revision(app, monkeypatch):
    from spedi import backup_ui
    monkeypatch.setattr(backup_ui.QMessageBox, 'question', lambda *a, **k: backup_ui.QMessageBox.StandardButton.Yes)
    revisions = [{'id': 42, 'created_at': '2026-10-29T12:00:00+00:00', 'reason': 'edit',
                  'summary': {'added': 0, 'removed': 0, 'changed': 1, 'axis_changed': False, 'title_changed': False}}]
    called = []
    dialog = backup_ui.ProfileHistoryDialog(revisions, called.append)
    dialog._restore()
    assert called == [42]
    dialog.deleteLater()


def test_shell_coalesces_update_checks_and_retires_worker(app, tmp_path, monkeypatch, wait_for):
    from brudnopis_ot.core.ddd_store import DddProfileStore
    from brudnopis_ot.ui import main_window
    from spedi.shell import ShellWindow
    from spedi.updates import UpdateService
    from tests.test_updates import release
    monkeypatch.setenv('SPEDI_DATA_DIR', str(tmp_path/'data'))
    store = DddProfileStore(tmp_path/'profiles.sqlite')
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    entered, proceed = threading.Event(), threading.Event()
    calls = []
    def fetch():
        calls.append(True)
        entered.set()
        assert proceed.wait(5)
        return release()
    window = ShellWindow()
    window._update_service = UpdateService(tmp_path/'updates.json', fetch=fetch)
    try:
        window._check_updates()
        wait_for(entered.is_set)
        window._check_updates()
        assert len(calls) == 1
        proceed.set()
        wait_for(lambda: window._update_worker is None)
        assert window._latest_release.version == '99.1.0'
        assert '99.1.0' in window._release_status.text()
    finally:
        proceed.set()
        if window._update_worker:
            assert window._update_worker.wait(5000)
            app.processEvents()
        window.close()
        window.deleteLater()
