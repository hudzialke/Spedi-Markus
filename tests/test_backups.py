from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from spedi.backups import BackupManager


@pytest.fixture
def manager(tmp_path):
    roots = {key: tmp_path/key for key in ('spedi', 'dispatcher')}
    for root in roots.values():
        root.mkdir()
        with closing(sqlite3.connect(root/'data.sqlite')) as conn:
            conn.execute('CREATE TABLE items (value TEXT)')
            conn.execute("INSERT INTO items VALUES ('original')")
            conn.commit()
        (root/'document.txt').write_text('original', encoding='utf-8')
    return BackupManager(roots, roots['spedi']/'backups')


def change(manager, value):
    for root in manager.sources.values():
        with closing(sqlite3.connect(root/'data.sqlite')) as conn:
            conn.execute('UPDATE items SET value=?', (value,))
            conn.commit()
        (root/'document.txt').write_text(value, encoding='utf-8')


def assert_value(manager, value):
    for root in manager.sources.values():
        with closing(sqlite3.connect(root/'data.sqlite')) as conn:
            assert conn.execute('SELECT value FROM items').fetchone()[0] == value
        assert (root/'document.txt').read_text(encoding='utf-8') == value


def test_complete_restore_is_deferred_and_has_a_reversible_pre_restore_copy(manager):
    archive = manager.create()
    change(manager, 'changed')
    manager.schedule_restore(archive)
    assert_value(manager, 'changed')
    assert manager.apply_pending() == 'restored'
    assert_value(manager, 'original')
    before = next(item for item in manager.list_backups() if item['reason'] == 'before_restore')
    manager.schedule_restore(before['path'])
    manager.apply_pending()
    assert_value(manager, 'changed')


def test_live_wal_data_is_backed_up_and_connections_are_closed(manager):
    path = manager.sources['spedi']/'data.sqlite'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("UPDATE items SET value='in WAL'")
        writer.commit()
        archive = manager.create(documents=False)
    manifest = manager.inspect(archive, verify=True)
    assert len(manifest['files']) == 2
    manager.schedule_restore(archive)
    manager.apply_pending()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute('SELECT value FROM items').fetchone()[0] == 'in WAL'


def test_credentials_and_the_backup_directory_are_excluded(manager):
    root = manager.sources['spedi']
    (root/'credentials.json').write_text('synthetic credential')
    (root/'token.dpapi').write_bytes(b'synthetic')
    (root/'firebase').mkdir()
    (root/'firebase/service-account.json').write_text('synthetic')
    first = manager.create()
    second = manager.create()
    with ZipFile(second) as zipped:
        assert len(zipped.namelist()) == 5
        assert first.name not in str(zipped.namelist())


def test_cancellation_never_leaves_a_partial_backup(manager):
    with pytest.raises(InterruptedError):
        manager.create(cancelled=lambda: True)
    assert not list(manager.directory.glob('*.partial'))
    assert manager.list_backups() == []


def test_failure_mid_restore_rolls_back_every_database_and_document(manager, monkeypatch):
    archive = manager.create()
    change(manager, 'newer')
    manager.schedule_restore(archive)
    original_install = manager._install
    calls = []
    def fail_once(path):
        calls.append(path)
        if len(calls) == 1:
            (manager.sources['spedi']/'document.txt').write_text('partially restored')
            with closing(sqlite3.connect(manager.sources['dispatcher']/'data.sqlite')) as conn:
                conn.execute("UPDATE items SET value='partially restored'")
                conn.commit()
            raise OSError('Synthetic interrupted restore')
        return original_install(path)
    monkeypatch.setattr(manager, '_install', fail_once)
    with pytest.raises(OSError, match='Synthetic interrupted'):
        manager.apply_pending()
    assert len(calls) == 2
    assert_value(manager, 'newer')
    assert not manager.journal.exists() and not manager.pending.exists()


def test_crash_journal_recovers_before_opening_stores(manager):
    before = manager.create('before_restore')
    change(manager, 'partial')
    introduced = manager.sources['spedi']/'introduced.txt'
    introduced.write_text('partial')
    manager.journal.write_text(json.dumps({'before': before.name, 'introduced': ['spedi/introduced.txt'], 'committed': False}))
    assert manager.apply_pending() == 'recovered'
    assert_value(manager, 'original')
    assert not introduced.exists()


def test_corrupt_archives_are_skipped_and_never_selected(manager):
    bad = manager.directory/'spedi-backup-broken.zip'
    bad.write_bytes(b'not a zip')
    assert manager.list_backups() == []
    with pytest.raises(Exception):
        manager.schedule_restore(bad)


@pytest.mark.parametrize('name', ['spedi/../../escape.txt', 'spedi/C:/escape.txt',
                                  'spedi/CON.txt', 'dispatcher/firebase/service-account.json'])
def test_restore_rejects_unsafe_paths_before_touching_data(manager, name):
    path = manager.directory/'spedi-backup-unsafe.zip'
    data = b'synthetic'
    with ZipFile(path, 'w') as zipped:
        zipped.writestr(name, data)
        zipped.writestr('manifest.json', json.dumps({'format': 1, 'files': [
            {'name': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'database': False}]}))
    with pytest.raises(ValueError):
        manager.schedule_restore(path)
    assert_value(manager, 'original')


def test_tampered_content_is_rejected(manager):
    archive = manager.create()
    altered = manager.directory/'spedi-backup-tampered.zip'
    with ZipFile(archive) as source, ZipFile(altered, 'w', compression=ZIP_DEFLATED) as dest:
        for name in source.namelist():
            content = source.read(name)
            dest.writestr(name, b'modified' if name == 'spedi/document.txt' else content)
    with pytest.raises(ValueError):
        manager.schedule_restore(altered)


def test_daily_backups_are_deduplicated_and_manual_ones_retained(manager):
    manual = manager.create()
    daily = manager.ensure_daily()
    assert daily and manager.ensure_daily() is None
    for _ in range(12):
        manager.create('before_import', documents=False)
    assert manual.exists()
    assert len([item for item in manager.list_backups() if item['reason'] == 'before_import']) == 10
