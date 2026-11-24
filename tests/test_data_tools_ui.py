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


def test_brudnopis_groups_commands_in_menus_and_watchlist_is_under_ddd(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QToolBar
    from PySide6.QtCore import Qt
    from brudnopis_ot.core.ddd_store import DddProfileStore
    from brudnopis_ot.ui import main_window
    from spedi.weekly_watch_ui import install_watchlist
    monkeypatch.setenv('SPEDI_DATA_DIR', str(tmp_path/'data'))
    store = DddProfileStore(tmp_path/'data'/'ddd_profiles.sqlite')
    monkeypatch.setattr(main_window, 'DddProfileStore', lambda: store)
    window = main_window.MainWindow()
    try:
        menus = [a.menu() for a in window.menuBar().actions()]
        assert [menu.title() for menu in menus] == ['Edycja', 'DDD', 'Widok']
        assert [a.text() for a in menus[0].actions() if not a.isSeparator()] == [
            'Nowy', 'Otwórz…', 'Zapisz', 'Zapisz jako…', 'Raport PDF', 'Backup danych…']
        action = install_watchlist(window)
        assert action in menus[1].actions()
        toolbars = window.findChildren(QToolBar, options=Qt.FindChildOption.FindDirectChildrenOnly)
        assert len(toolbars) == 1
        assert action not in toolbars[0].actions()
        view = {a.text() for a in menus[2].actions()}
        assert {'Dodaj tydzień', 'Usuń ostatni tydzień', 'Usuń pierwszy tydzień'} <= view
        window._dirty = True
        monkeypatch.setattr(window, '_save', lambda: None)
        opened = []
        from spedi import backups
        monkeypatch.setattr(backups, 'application_backups', lambda: opened.append(True))
        window._open_backups()
        assert opened == []  # A failed/cancelled save must not create a stale snapshot.
    finally:
        window._dirty = False
        window.close()
        window.deleteLater()


def test_backup_dialog_exports_a_verified_7z_to_the_chosen_folder(app, manager, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from spedi import backup_ui
    monkeypatch.setattr(backup_ui, 'run_background', lambda parent, label, action, **kw:
        action(SimpleNamespace(isInterruptionRequested=lambda: False)))
    dialog = backup_ui.BackupDialog(manager)
    try:
        folder = tmp_path/'chosen-folder'
        dialog.folder.setText(str(folder))
        dialog.create()
        archives = list(folder.glob('*.7z'))
        assert len(archives) == 1
        assert len(manager.inspect(archives[0], verify=True)['files']) == 4
        assert str(archives[0]) in dialog.result.text()
        assert dialog.list.count() == 1
        assert not manager.pending.exists()
    finally:
        dialog.reject()
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


def test_drive_api_denial_is_detected_before_backup_compression(app, manager, monkeypatch):
    from types import SimpleNamespace
    from spedi import backup_ui, backup_drive
    error = backup_drive.DriveAPIError(403, {'error': {'errors': [{'reason': 'accessNotConfigured'}]}})
    def denied():
        raise error
    monkeypatch.setattr(backup_drive, 'BackupDrive', lambda: SimpleNamespace(check_access=denied))
    monkeypatch.setattr(backup_ui, 'run_background', lambda parent, label, action, **kw:
        action(SimpleNamespace(isInterruptionRequested=lambda: False)))
    dialog = backup_ui.BackupDialog(manager)
    errors, creations = [], []
    monkeypatch.setattr(dialog, '_drive_error', errors.append)
    monkeypatch.setattr(dialog, '_create', lambda: creations.append(True))
    try:
        dialog.create_drive()
        assert errors == [error]
        assert not creations
        assert not list(manager.directory.glob('*.7z'))
    finally:
        dialog.reject()
        dialog.deleteLater()
