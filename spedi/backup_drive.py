"""Backup transfers using the program's existing drive.file OAuth connection."""
from contextlib import closing
import hashlib
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse
from uuid import uuid4

import requests

from spedi.i18n import tr

_API = 'https://www.googleapis.com/drive/v3/files'
_UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
_QUERY = "trashed = false and appProperties has { key='spediBackup' and value='1' }"
_FIELDS = 'id,name,size,md5Checksum,createdTime,webViewLink'


class DriveAPIError(RuntimeError):
    """Expose a useful provider reason without credentials or raw response bodies."""
    def __init__(self, status, payload):
        error = payload.get('error', {}) if isinstance(payload, dict) else {}
        if not isinstance(error, dict):
            error = {}
        legacy = error.get('errors', [])
        details = error.get('details', [])
        reasons = {item.get('reason') for items in (legacy, details) if isinstance(items, list)
                   for item in items if isinstance(item, dict) and isinstance(item.get('reason'), str)}
        self.status = status
        self.help_url = None
        self.retryable = status == 429 or status >= 500
        self.reason = 'unknown'
        project = None
        for item in details if isinstance(details, list) else []:
            metadata = item.get('metadata', {}) if isinstance(item, dict) else {}
            if isinstance(metadata, dict) and re.fullmatch(r'projects/\d+', str(metadata.get('consumer', ''))):
                project = metadata['consumer'].split('/')[1]
        if reasons & {'SERVICE_DISABLED', 'accessNotConfigured', 'API_DISABLED'}:
            self.reason = 'SERVICE_DISABLED'
            hint = tr('Logowanie Google zakończyło się, ale Google Drive API jest wyłączone w projekcie aplikacji. '
                      'Włącz Google Drive API w tym samym projekcie Google Cloud, z którego pochodzi klient OAuth. '
                      'Następnie odczekaj kilka minut i kliknij „Odśwież kopie Google”. Ponowne logowanie nie włącza API.')
            self.help_url = 'https://console.cloud.google.com/apis/library/drive.googleapis.com'
            if project:
                self.help_url += '?project='+project
        elif reasons & {'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'insufficientPermissions', 'insufficientAuthenticationScopes'}:
            self.reason = 'ACCESS_TOKEN_SCOPE_INSUFFICIENT'
            hint = tr('Google nie przyznał programowi dostępu do plików Dysku. Kliknij „Połącz / ponów logowanie” '
                      'i zaakceptuj zgodę na pliki tworzone przez aplikację. Jeśli zgoda jest blokowana, '
                      'sprawdź konfigurację OAuth lub skontaktuj się z administratorem konta Google Workspace.')
        elif reasons & {'storageQuotaExceeded'}:
            self.reason = 'storageQuotaExceeded'
            hint = tr('Na Dysku Google zabrakło miejsca. Zwolnij miejsce i ponów wysyłanie. Kopia lokalna pozostaje zachowana.')
        elif reasons & {'rateLimitExceeded', 'userRateLimitExceeded', 'RATE_LIMIT_EXCEEDED'} or status == 429:
            self.reason = 'rateLimitExceeded'
            self.retryable = True
            hint = tr('Google ograniczył liczbę żądań. Odczekaj chwilę i spróbuj ponownie.')
        elif reasons & {'dailyLimitExceeded'}:
            self.reason = 'dailyLimitExceeded'
            hint = tr('Wyczerpano dzienny limit Google Drive API dla projektu. Sprawdź limity w Google Cloud lub spróbuj później.')
        elif reasons & {'domainPolicy', 'ORG_RESTRICTION_VIOLATION'}:
            self.reason = 'domainPolicy'
            hint = tr('Administrator konta Google Workspace blokuje dostęp tej aplikacji do Dysku. Poproś administratora o odblokowanie aplikacji.')
        elif reasons & {'appNotAuthorizedToFile', 'insufficientFilePermissions'}:
            self.reason = 'insufficientFilePermissions'
            hint = tr('Program nie ma uprawnień do wybranego pliku lub folderu Google. Sprawdź konto i dostęp do tej kopii.')
        elif status == 401:
            self.reason = 'UNAUTHENTICATED'
            hint = tr('Google odrzucił zapisane logowanie. Kliknij „Połącz / ponów logowanie” i zaloguj się ponownie.')
        elif status >= 500:
            hint = tr('Usługa Dysku Google jest chwilowo niedostępna. Spróbuj ponownie później.')
        else:
            hint = tr('Google odrzucił żądanie. Sprawdź, czy Google Drive API jest włączone w projekcie aplikacji, '
                      'udzielono zgody na dostęp do Dysku i konto nie ma ograniczeń administratora.')
        super().__init__(f"{tr('Dysk Google')}: {status} ({self.reason}).\n\n{hint}")


def _cancel(cancelled):
    if cancelled():
        raise InterruptedError(tr('Anulowano przesyłanie kopii.'))


class BackupDrive:
    def __init__(self, token=None, session=None):
        from markus_dispatcher.core.google_connection import access_token
        self.token = token or access_token
        self.session = session or requests.Session()

    def _request(self, method, url, **kwargs):
        response = self.session.request(method, url, timeout=(15, 90),
            headers={'Authorization': 'Bearer '+self.token(), **kwargs.pop('headers', {})}, **kwargs)
        if response.status_code >= 400:
            try:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                raise DriveAPIError(response.status_code, payload)
            finally:
                response.close()
        return response

    def check_access(self):
        # A token alone only proves sign-in; exercise Drive before making a backup.
        with closing(self._request('GET', _API, params={
            'q': 'trashed = false', 'pageSize': 1, 'fields': 'kind',
        })):
            pass

    def _list(self, query, fields, *, cancelled=lambda: False):
        result, page = [], None
        while True:
            _cancel(cancelled)
            with closing(self._request('GET', _API, params={
                'q': query, 'fields': f'nextPageToken,files({fields})',
                'pageSize': 100, 'pageToken': page, 'orderBy': 'createdTime desc',
            })) as response:
                data = response.json()
            result.extend(data.get('files', []))
            page = data.get('nextPageToken')
            if not page:
                return result

    def list_backups(self, *, cancelled=lambda: False):
        return self._list(_QUERY, _FIELDS, cancelled=cancelled)

    def _folder(self):
        query = ("trashed = false and mimeType = 'application/vnd.google-apps.folder' and "
                 "appProperties has { key='spediBackupFolder' and value='1' }")
        folders = self._list(query, 'id')
        if folders:
            return folders[0]['id']
        with closing(self._request('POST', _API, params={'fields': 'id'}, json={
            'name': 'Spedi — backupy', 'mimeType': 'application/vnd.google-apps.folder',
            'appProperties': {'spediBackupFolder': '1'},
        })) as response:
            return response.json()['id']

    def upload(self, path, *, cancelled=lambda: False):
        path = Path(path)
        _cancel(cancelled)
        total = path.stat().st_size
        with path.open('rb') as source:
            checksum = hashlib.file_digest(source, 'md5').hexdigest()
        with closing(self._request('POST', _UPLOAD,
            params={'uploadType': 'resumable', 'fields': _FIELDS},
            headers={'X-Upload-Content-Type': 'application/x-7z-compressed', 'X-Upload-Content-Length': str(total)},
            json={'name': path.name, 'parents': [self._folder()],
                  'appProperties': {'spediBackup': '1'}},
        )) as response:
            location = response.headers['Location']
        parsed = urlparse(location)
        if parsed.scheme != 'https' or parsed.hostname != 'www.googleapis.com':
            raise ValueError(tr('Nieprawidłowy adres przesyłania Google.'))
        offset, failures = 0, 0
        with path.open('rb') as source:
            while offset < total:
                _cancel(cancelled)
                source.seek(offset)
                block = source.read(8*1024**2)
                try:
                    response = self._request('PUT', location, data=block,
                        headers={'Content-Range': f'bytes {offset}-{offset+len(block)-1}/{total}',
                                 'Content-Type': 'application/x-7z-compressed'})
                except (requests.RequestException, DriveAPIError) as error:
                    if isinstance(error, DriveAPIError) and not error.retryable:
                        raise
                    failures += 1
                    if failures > 3:
                        raise
                    _cancel(cancelled)
                    # Ask Drive how many bytes arrived before retrying the next chunk.
                    response = self._request('PUT', location, data=b'',
                        headers={'Content-Range': f'bytes */{total}'})
                with closing(response):
                    if response.status_code in (200, 201):
                        result = response.json()
                        if result.get('md5Checksum') != checksum or int(result.get('size', -1)) != total:
                            raise ValueError(tr('Dysk Google nie potwierdził integralności kopii. Kopia lokalna została zachowana.'))
                        return result
                    if response.status_code != 308:
                        raise RuntimeError(tr('Dysk Google przerwał przesyłanie kopii.'))
                    match = re.fullmatch(r'bytes=0-(\d+)', response.headers.get('Range', ''))
                    received = int(match[1])+1 if match else 0
                    if received < offset or received > offset+len(block) or received >= total:
                        raise ValueError(tr('Nieprawidłowe potwierdzenie przesyłania Google.'))
                    if received == offset:
                        failures += 1
                        if failures > 3:
                            raise RuntimeError(tr('Dysk Google nie przyjmuje danych kopii.'))
                    offset = received
        raise RuntimeError(tr('Dysk Google nie potwierdził zapisania kopii.'))

    def download(self, item, directory, *, cancelled=lambda: False):
        from spedi.backups import _MAX_BYTES
        identifier = item['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
            raise ValueError(tr('Nieprawidłowy identyfikator kopii Google.'))
        size = int(item['size'])
        if size < 0 or size > _MAX_BYTES:
            raise ValueError(tr('Kopia przekracza limit rozmiaru.'))
        if shutil.disk_usage(directory).free < size + 64*1024**2:
            raise OSError(tr('Za mało miejsca na pobranie kopii Google.'))
        destination = Path(directory)/f'spedi-backup-drive-{uuid4().hex}.7z'
        temporary = destination.with_suffix('.partial')
        digest, received = hashlib.md5(), 0
        try:
            _cancel(cancelled)
            with closing(self._request('GET', _API+'/'+identifier, params={'alt': 'media'}, stream=True)) as response:
                with temporary.open('wb') as output:
                    for block in response.iter_content(1024**2):
                        _cancel(cancelled)
                        received += len(block)
                        if received > size:
                            raise ValueError(tr('Rozmiar kopii Google nie zgadza się z opisem.'))
                        output.write(block)
                        digest.update(block)
            if received != size or digest.hexdigest() != item.get('md5Checksum'):
                raise ValueError(tr('Suma kontrolna pobranej kopii jest nieprawidłowa.'))
            temporary.replace(destination)
            return destination
        finally:
            temporary.unlink(missing_ok=True)
