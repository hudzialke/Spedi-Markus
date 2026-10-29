from copy import deepcopy

import pytest

from spedi.updates import UpdateService, PROJECT_URL, version_tuple


def release(tag='v99.1.0'):
    return {'tag_name': tag, 'draft': False, 'prerelease': False,
            'html_url': PROJECT_URL+'/releases/tag/'+tag, 'body': 'Synthetic notes',
            'assets': [{'name': 'Spedi-Setup.exe', 'browser_download_url': PROJECT_URL+'/releases/download/'+tag+'/Spedi-Setup.exe'}]}


def test_versions_are_numeric_and_only_stable_tags_are_accepted():
    assert version_tuple('0.10.0') > version_tuple('v0.9.9')
    for invalid in ('v1.0.0-beta', '../v1.0.0', '1.0', 'version1'):
        with pytest.raises(ValueError):
            version_tuple(invalid)


def test_success_is_cached_and_daily_check_respects_user_preference(tmp_path):
    clock = [1_000_000]
    service = UpdateService(tmp_path/'updates.json', fetch=release, now=lambda: clock[0])
    assert service.due()
    result = service.check()
    assert result.newer_than() and not service.due()
    assert service.cached() == result
    clock[0] += 86_400
    assert service.due()
    service.set_automatic(False)
    assert not service.due()
    assert service.check() == result  # Explicit checks remain available.
    assert not service.automatic


@pytest.mark.parametrize('change', [
    lambda data: data.update(draft=True), lambda data: data.update(prerelease=True),
    lambda data: data.update(html_url='https://example.invalid/release'),
    lambda data: data['assets'][0].update(browser_download_url='file:///synthetic.exe'),
    lambda data: data['assets'][0].update(browser_download_url=PROJECT_URL+'/releases/download/v0.1.0/Spedi-Setup.exe'),
    lambda data: data.update(assets=[]),
])
def test_untrusted_or_incomplete_release_is_never_offered(tmp_path, change):
    data = release()
    change(data)
    service = UpdateService(tmp_path/'updates.json', fetch=lambda: data)
    with pytest.raises(ValueError):
        service.check()
    assert not service.path.exists()


def test_network_failure_does_not_mark_the_app_up_to_date(tmp_path):
    def fail():
        raise OSError('Synthetic offline error')
    service = UpdateService(tmp_path/'updates.json', fetch=fail)
    with pytest.raises(OSError):
        service.check()
    assert service.due() and service.cached() is None


def test_download_requires_a_successful_backup_and_never_executes_installer(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from spedi import update_ui
    app = QApplication.instance() or QApplication([])
    service = UpdateService(tmp_path/'updates.json', fetch=release)
    info = service.check()
    events = []
    class Backups:
        def create(self, *args, **kwargs):
            events.append('backup')
    monkeypatch.setattr(update_ui, 'run_background', lambda parent, label, action: action(None))
    monkeypatch.setattr(update_ui.QDesktopServices, 'openUrl', lambda url: events.append(url.toString()))
    dialog = update_ui.UpdateDialog(info, service, Backups())
    dialog._download()
    assert events == ['backup', info.installer_url]
    def fail(*args, **kwargs):
        raise OSError('Synthetic full disk')
    monkeypatch.setattr(Backups, 'create', fail)
    monkeypatch.setattr(update_ui.QMessageBox, 'warning', lambda *args: None)
    dialog._download()
    assert len(events) == 2
    dialog.deleteLater()
