"""Local, verified backups. Never reads application storage merely on import."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
from uuid import uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from spedi import __version__

_DATABASES = {'.db', '.sqlite', '.sqlite3'}
_SKIP_DIRS = {'backups', 'firebase', '__pycache__', 'cache', 'logs'}
_SKIP_NAMES = {'service-account.json', 'config.json', 'google-services.json'}
_REASONS = {'manual', 'daily', 'before_import', 'before_update', 'before_restore'}
_MAX_BYTES = 50 * 1024**3


def _private(path):
    name = path.name.casefold()
    return (name in _SKIP_NAMES or path.suffix.lower() in {'.dpapi', '.log', '.tmp', '.lock', '.key', '.pem', '.pfx', '.p12', '.jks', '.keystore'}
            or name.startswith(('credentials', 'client_secret', 'service-account', 'service_account', 'token.')))


def _digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _write_json(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def _safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or '\\' in name or path.as_posix() != name:
        raise ValueError('Nieprawidłowa ścieżka w kopii.')
    for part in path.parts:
        if (part in {'.', '..'} or part.endswith(('.', ' '))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
            raise ValueError('Nieprawidłowa ścieżka w kopii.')
    return path


class BackupManager:
    def __init__(self, sources, directory):
        self.sources = {key: Path(value).resolve() for key, value in sources.items()}
        if any(not key or '/' in key or '\\' in key or key in {'.', '..'} for key in self.sources):
            raise ValueError('Nieprawidłowy katalog danych.')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pending = self.directory/'pending-restore.json'
        self.journal = self.directory/'restore-journal.json'

    def _target(self, name):
        parts = _safe_name(name).parts
        if len(parts) < 2 or parts[0] not in self.sources or {p.casefold() for p in parts[1:]} & _SKIP_DIRS:
            raise ValueError('Kopia zawiera nieobsługiwany katalog.')
        root = self.sources[parts[0]]
        target = root.joinpath(*parts[1:])
        for parent in (target, *target.parents):
            if parent == root.parent:
                break
            if parent.is_symlink():
                raise ValueError('Kopia nie obsługuje dowiązań.')
        if not target.resolve().is_relative_to(root) or target.is_dir() or _private(target):
            raise ValueError('Nieprawidłowy cel przywracania.')
        return target

    def create(self, reason='manual', *, documents=True, cancelled=lambda: False):
        if reason not in _REASONS:
            raise ValueError('Nieznany rodzaj kopii.')
        identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:10]
        destination = self.directory/f'spedi-backup-{identifier}.zip'
        temporary = destination.with_suffix('.partial')
        entries = []
        total_bytes = 0
        try:
            with tempfile.TemporaryDirectory(dir=self.directory, prefix='.snapshot-') as scratch:
                with ZipFile(temporary, 'w', compression=ZIP_DEFLATED, compresslevel=1) as archive:
                    for label, root in self.sources.items():
                        if not root.is_dir():
                            continue
                        for folder, dirs, files in os.walk(root, followlinks=False):
                            dirs[:] = [d for d in sorted(dirs) if d.casefold() not in _SKIP_DIRS
                                       and not (Path(folder)/d).is_symlink()
                                       and (Path(folder)/d).resolve() != self.directory]
                            for filename in sorted(files):
                                if cancelled():
                                    raise InterruptedError('Anulowano tworzenie kopii.')
                                path = Path(folder)/filename
                                if (path.is_symlink() or _private(path)
                                        or path.name.endswith(('-wal', '-shm', '-journal'))):
                                    continue
                                database = path.suffix.lower() in _DATABASES
                                if not documents and not database:
                                    continue
                                raw_size = path.stat().st_size
                                wal = path.with_name(path.name+'-wal')
                                if database and wal.exists():
                                    raw_size += wal.stat().st_size
                                if shutil.disk_usage(self.directory).free < raw_size*(2 if database else 1)+512*1024**2:
                                    raise OSError('Za mało wolnego miejsca na bezpieczną kopię danych.')
                                name = label+'/'+path.relative_to(root).as_posix()
                                _safe_name(name)
                                source = path
                                if database:
                                    source = Path(scratch)/f'{len(entries)}.sqlite'
                                    def progress(*_):
                                        if cancelled():
                                            raise InterruptedError('Anulowano tworzenie kopii.')
                                    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as src:
                                        with closing(sqlite3.connect(source)) as dst:
                                            src.backup(dst, pages=256, progress=progress, sleep=.02)
                                before = source.stat()
                                if len(entries) >= 100_000 or total_bytes+before.st_size > _MAX_BYTES:
                                    raise ValueError('Kopia przekracza limit rozmiaru.')
                                digest = hashlib.sha256()
                                size = 0
                                with source.open('rb') as stream, archive.open(name, 'w', force_zip64=True) as output:
                                    for block in iter(lambda: stream.read(1024**2), b''):
                                        if cancelled():
                                            raise InterruptedError('Anulowano tworzenie kopii.')
                                        output.write(block)
                                        digest.update(block)
                                        size += len(block)
                                after = source.stat()
                                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                                    raise RuntimeError('Plik zmienił się podczas kopiowania. Ponów kopię.')
                                entries.append({'name': name, 'bytes': size, 'sha256': digest.hexdigest(),
                                                'database': database})
                                total_bytes += size
                    manifest = {'format': 1, 'app_version': __version__, 'reason': reason,
                                'created_at': datetime.now(timezone.utc).isoformat(),
                                'documents': documents, 'files': entries}
                    archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            temporary.replace(destination)
            self.prune()
            return destination
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def inspect(self, path, *, verify=False):
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 100_001 or sum(info.file_size for info in infos) > _MAX_BYTES:
                raise ValueError('Kopia przekracza limit rozmiaru.')
            if len({info.filename.casefold() for info in infos}) != len(infos):
                raise ValueError('Kopia zawiera powtarzające się ścieżki.')
            if archive.getinfo('manifest.json').file_size > 20 * 1024**2:
                raise ValueError('Zbyt duży opis kopii.')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest.get('format') != 1 or not isinstance(manifest.get('files'), list):
                raise ValueError('Nieobsługiwany format kopii.')
            if manifest.get('reason') not in _REASONS or type(manifest.get('documents')) is not bool:
                raise ValueError('Nieprawidłowy opis kopii.')
            datetime.fromisoformat(manifest['created_at'])
            files = manifest['files']
            if len({item['name'].casefold() for item in files}) != len(files):
                raise ValueError('Kopia zawiera powtarzające się ścieżki.')
            if {info.filename for info in infos} != {'manifest.json', *(item['name'] for item in files)}:
                raise ValueError('Zawartość kopii nie zgadza się z opisem.')
            for item in files:
                target = self._target(item['name'])
                info = archive.getinfo(item['name'])
                if (info.file_size != item['bytes'] or ((info.external_attr >> 16) & 0o170000) == 0o120000
                        or bool(item['database']) != (target.suffix.lower() in _DATABASES)):
                    raise ValueError('Nieprawidłowy opis pliku w kopii.')
                if verify:
                    digest = hashlib.sha256()
                    with archive.open(info) as stream:
                        for block in iter(lambda: stream.read(1024**2), b''):
                            digest.update(block)
                    if digest.hexdigest() != item['sha256']:
                        raise ValueError('Suma kontrolna kopii jest nieprawidłowa.')
            return manifest

    def list_backups(self):
        result = []
        for path in self.directory.glob('spedi-backup-*.zip'):
            try:
                data = self.inspect(path)
                result.append({'path': path, **{key: data[key] for key in ('created_at', 'reason', 'documents')},
                               'bytes': path.stat().st_size})
            except (OSError, ValueError, KeyError, BadZipFile, RuntimeError):
                continue
        return sorted(result, key=lambda item: item['created_at'], reverse=True)

    def prune(self):
        limits = {'daily': 7, 'before_import': 10, 'before_update': 5, 'before_restore': 3}
        counts = {}
        protected = set()
        for path, key in ((self.pending, 'archive'), (self.journal, 'before')):
            if path.exists():
                protected.add(json.loads(path.read_text(encoding='utf-8'))[key])
        for item in self.list_backups():
            reason = item['reason']
            counts[reason] = counts.get(reason, 0)+1
            if reason in limits and counts[reason] > limits[reason] and item['path'].name not in protected:
                item['path'].unlink(missing_ok=True)

    def ensure_daily(self, *, cancelled=lambda: False):
        today = datetime.now(timezone.utc).date().isoformat()
        if any(item['reason'] == 'daily' and item['created_at'].startswith(today) for item in self.list_backups()):
            return None
        return self.create('daily', cancelled=cancelled)

    def schedule_restore(self, path):
        path = Path(path).resolve()
        self.inspect(path, verify=True)
        if path.parent != self.directory:
            imported = self.directory/f'spedi-backup-imported-{uuid4().hex}.zip'
            shutil.copy2(path, imported)
            path = imported
        _write_json(self.pending, {'archive': path.name, 'sha256': _digest(path)})

    def _install(self, path):
        manifest = self.inspect(path, verify=True)
        with tempfile.TemporaryDirectory(dir=self.directory, prefix='.restore-') as scratch:
            with ZipFile(path) as archive:
                for index, item in enumerate(manifest['files']):
                    target = self._target(item['name'])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    staged = Path(scratch)/str(index)
                    with archive.open(item['name']) as source, staged.open('wb') as output:
                        shutil.copyfileobj(source, output, length=1024**2)
                    if item['database']:
                        with closing(sqlite3.connect(staged.as_uri()+'?mode=ro', uri=True)) as source:
                            if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                                raise ValueError('Baza w kopii jest uszkodzona.')
                            with closing(sqlite3.connect(target)) as dest:
                                source.backup(dest)
                    else:
                        sibling = target.with_name(target.name+'.restore-'+uuid4().hex)
                        try:
                            shutil.copyfile(staged, sibling)
                            sibling.replace(target)
                        finally:
                            sibling.unlink(missing_ok=True)

    def apply_pending(self):
        """Only before stores/widgets open. Interrupted attempts roll back first."""
        if self.journal.exists():
            journal = json.loads(self.journal.read_text(encoding='utf-8'))
            if not journal.get('committed'):
                self._install(self.directory/_safe_name(journal['before']).name)
                for name in journal['introduced']:
                    self._target(name).unlink(missing_ok=True)
            self.journal.unlink()
            self.pending.unlink(missing_ok=True)
            return 'recovered' if not journal.get('committed') else 'restored'
        if not self.pending.exists():
            return None
        plan = json.loads(self.pending.read_text(encoding='utf-8'))
        path = self.directory/_safe_name(plan['archive']).name
        if _digest(path) != plan['sha256']:
            raise ValueError('Wybrana kopia zmieniła się od momentu wyboru.')
        manifest = self.inspect(path, verify=True)
        before = self.create('before_restore', documents=manifest['documents'])
        journal = {'before': before.name, 'introduced': [item['name'] for item in manifest['files']
                   if not self._target(item['name']).exists()], 'committed': False}
        _write_json(self.journal, journal)
        try:
            self._install(path)
            journal['committed'] = True
            _write_json(self.journal, journal)
        except Exception:
            self.apply_pending()  # Restore every original DB/file before continuing.
            raise
        self.pending.unlink(missing_ok=True)
        self.journal.unlink()
        return 'restored'


def application_backups():
    from spedi.paths import data_dir
    from markus_dispatcher import config
    root = data_dir()
    return BackupManager({'spedi': root, 'dispatcher': config.DATA_DIR}, root/'backups')


def before_profile_import(store, *, cancelled=lambda: False):
    if store.list_profile_summaries():
        root = store.path.parent
        BackupManager({'spedi': root}, root/'backups').create('before_import', documents=False, cancelled=cancelled)
