import hashlib

import pytest
import requests

from spedi.backup_drive import BackupDrive, DriveAPIError


class Response:
    def __init__(self, status=200, data=None, headers=None, content=b''):
        self.status_code = status
        self.data = data or {}
        self.headers = headers or {}
        self.content = content
        self.closed = False

    def json(self):
        return self.data

    def close(self):
        self.closed = True

    def iter_content(self, size):
        for offset in range(0, len(self.content), size):
            yield self.content[offset:offset+size]


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_upload_retries_from_google_acknowledged_offset_and_verifies_checksum(tmp_path):
    content = b'7z-content'*100
    path = tmp_path/'spedi-backup-all.7z'
    path.write_bytes(content)
    result = {'id': 'file-id', 'size': str(len(content)),
              'md5Checksum': hashlib.md5(content).hexdigest()}
    responses = [Response(data={'files': [{'id': 'folder-id'}]}),
                 Response(headers={'Location': 'https://www.googleapis.com/upload/drive/v3/files?upload_id=example'}),
                 requests.ConnectionError('interrupted'),
                 Response(308, headers={'Range': 'bytes=0-511'}), Response(data=result)]
    session = Session(responses)
    drive = BackupDrive(token=lambda: 'synthetic-token', session=session)
    assert drive.upload(path) == result
    assert session.calls[3][2]['headers']['Content-Range'] == f'bytes */{len(content)}'
    assert session.calls[4][2]['data'] == content[512:]
    assert session.calls[4][2]['headers']['Content-Range'] == f'bytes 512-{len(content)-1}/{len(content)}'
    assert session.calls[1][2]['json']['parents'] == ['folder-id']
    assert all(item.closed for item in responses if isinstance(item, Response))


def test_upload_requires_remote_checksum_and_keeps_local_copy(tmp_path):
    path = tmp_path/'all.7z'
    path.write_bytes(b'local backup')
    session = Session([Response(data={'files': []}), Response(data={'id': 'folder'}),
        Response(headers={'Location': 'https://www.googleapis.com/upload/example'}),
        Response(data={'size': '12', 'md5Checksum': 'wrong'})])
    with pytest.raises(ValueError, match='integralności'):
        BackupDrive(token=lambda: 'synthetic', session=session).upload(path)
    assert path.read_bytes() == b'local backup'


def test_backup_list_is_paged_and_limited_to_application_files():
    session = Session([Response(data={'files': [{'id': 'first'}], 'nextPageToken': 'next'}),
                       Response(data={'files': [{'id': 'second'}]})])
    drive = BackupDrive(token=lambda: 'synthetic', session=session)
    assert drive.list_backups() == [{'id': 'first'}, {'id': 'second'}]
    assert 'spediBackup' in session.calls[0][2]['params']['q']
    assert session.calls[1][2]['params']['pageToken'] == 'next'


@pytest.mark.parametrize('bad', [False, True])
def test_download_checks_content_and_removes_partial_files(tmp_path, bad):
    content = b'original backup'
    item = {'id': 'drive-file', 'size': str(len(content)), 'md5Checksum': hashlib.md5(content).hexdigest()}
    response = Response(content=b'modified backup' if bad else content)
    drive = BackupDrive(token=lambda: 'synthetic', session=Session([response]))
    if bad:
        with pytest.raises(ValueError):
            drive.download(item, tmp_path)
        assert not list(tmp_path.iterdir())
    else:
        assert drive.download(item, tmp_path).read_bytes() == content
    assert response.closed


def test_cancelled_download_performs_no_network_or_file_write(tmp_path):
    session = Session([])
    drive = BackupDrive(token=lambda: 'synthetic', session=session)
    with pytest.raises(InterruptedError):
        drive.download({'id': 'file', 'size': '12'}, tmp_path, cancelled=lambda: True)
    assert not session.calls
    assert not list(tmp_path.iterdir())


def test_http_errors_do_not_expose_credentials_or_provider_response():
    session = Session([Response(403, data={'private': 'provider detail'})])
    with pytest.raises(RuntimeError) as exc:
        BackupDrive(token=lambda: 'synthetic-secret', session=session).list_backups()
    assert '403' in str(exc.value)
    assert 'synthetic-secret' not in str(exc.value) and 'provider detail' not in str(exc.value)


@pytest.mark.parametrize('payload', [
    {'error': {'errors': [{'reason': 'accessNotConfigured'}]}},
    {'error': {'details': [{'reason': 'SERVICE_DISABLED', 'metadata': {
        'service': 'drive.googleapis.com', 'consumer': 'projects/123456789',
        'activationUrl': 'https://untrusted.example/ignore',
    }}]}},
])
def test_disabled_drive_api_has_an_actionable_error_and_closes_response(payload):
    response = Response(403, data=payload)
    session = Session([response])
    with pytest.raises(DriveAPIError) as exc:
        BackupDrive(token=lambda: 'synthetic', session=session).check_access()
    error = exc.value
    assert error.reason == 'SERVICE_DISABLED'
    assert not error.retryable
    assert error.help_url.startswith('https://console.cloud.google.com/apis/library/drive.googleapis.com')
    assert 'untrusted' not in error.help_url
    if 'details' in payload['error']:
        assert error.help_url.endswith('?project=123456789')
    assert response.closed


@pytest.mark.parametrize('reason,expected', [
    ('ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT'),
    ('insufficientPermissions', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT'),
    ('storageQuotaExceeded', 'storageQuotaExceeded'),
    ('domainPolicy', 'domainPolicy'),
])
def test_forbidden_causes_are_distinguished(reason, expected):
    error = DriveAPIError(403, {'error': {'errors': [{'reason': reason}]}})
    assert error.reason == expected
    assert not error.retryable


def test_permission_error_during_upload_is_not_retried_as_a_network_failure(tmp_path):
    path = tmp_path/'all.7z'
    path.write_bytes(b'local backup')
    session = Session([Response(data={'files': [{'id': 'folder'}]}),
        Response(headers={'Location': 'https://www.googleapis.com/upload/example'}),
        Response(403, data={'error': {'errors': [{'reason': 'insufficientPermissions'}]}})])
    with pytest.raises(DriveAPIError) as exc:
        BackupDrive(token=lambda: 'synthetic', session=session).upload(path)
    assert exc.value.reason == 'ACCESS_TOKEN_SCOPE_INSUFFICIENT'
    assert len(session.calls) == 3
    assert path.read_bytes() == b'local backup'


@pytest.mark.parametrize('payload', [[], {'error': 'invalid'}, {'error': {'details': 'invalid', 'errors': 123}}])
def test_unstructured_provider_errors_have_a_safe_fallback(payload):
    error = DriveAPIError(403, payload)
    assert error.reason == 'unknown'
    assert '403' in str(error)


def test_access_check_reaches_drive_without_creating_or_listing_user_files():
    session = Session([Response(data={'kind': 'drive#fileList'})])
    BackupDrive(token=lambda: 'synthetic', session=session).check_access()
    method, url, args = session.calls[0]
    assert method == 'GET'
    assert args['params']['fields'] == 'kind'
