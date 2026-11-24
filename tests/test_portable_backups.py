from contextlib import closing
import sqlite3

import py7zr
import pytest

from spedi.backups import BackupManager
from tests.test_backups import manager, change, assert_value


def test_7z_roundtrip_on_another_computer_rebases_managed_paths(manager, tmp_path):
    root = manager.sources['spedi']
    document = root/'ddd_files'/'original.ddd'
    document.parent.mkdir()
    document.write_bytes(b'original DDD contents')
    with closing(sqlite3.connect(root/'data.sqlite')) as conn:
        for table in ('archived_files', 'attachments'):
            conn.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY, stored_path TEXT)')
            conn.execute(f'INSERT INTO {table}(stored_path) VALUES (?)', (str(document),))
        conn.commit()
    with closing(sqlite3.connect(root/'data.sqlite')) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("UPDATE items SET value='committed WAL'")
        writer.commit()
        archive = manager.export_all(tmp_path/'usb'/'all.7z')
    assert archive.read_bytes().startswith(b'7z\xbc\xaf\x27\x1c')
    assert len(manager.inspect(archive, verify=True)['files']) == 5
    roots = {key: tmp_path/'new-computer'/key for key in manager.sources}
    destination = BackupManager(roots, roots['spedi']/'backups')
    destination.schedule_restore(archive)
    assert not (roots['spedi']/'data.sqlite').exists()
    assert destination.apply_pending() == 'restored'
    moved = roots['spedi']/'ddd_files'/'original.ddd'
    assert moved.read_bytes() == document.read_bytes()
    with closing(sqlite3.connect(roots['spedi']/'data.sqlite')) as conn:
        assert conn.execute('SELECT value FROM items').fetchone()[0] == 'committed WAL'
        for table in ('archived_files', 'attachments'):
            assert conn.execute(f'SELECT stored_path FROM {table}').fetchone()[0] == str(moved)
    with closing(sqlite3.connect(roots['dispatcher']/'data.sqlite')) as conn:
        assert conn.execute('SELECT value FROM items').fetchone()[0] == 'original'


def test_unpacked_backup_can_be_imported_and_is_copied_before_restart(manager, tmp_path):
    archive = manager.export_all(tmp_path/'all.7z')
    unpacked = tmp_path/'unpacked'
    with py7zr.SevenZipFile(archive) as source:
        source.extractall(unpacked)
    change(manager, 'changed')
    manager.schedule_restore(unpacked)
    (unpacked/'spedi'/'document.txt').write_text('modified after import')
    manager.apply_pending()
    assert_value(manager, 'original')


def test_export_cancellation_keeps_existing_destination_and_cleans_up(manager, tmp_path):
    target = tmp_path/'keep.7z'
    target.write_bytes(b'previous backup')
    with pytest.raises(InterruptedError):
        manager.export_all(target, cancelled=lambda: True)
    assert target.read_bytes() == b'previous backup'
    assert not list(target.parent.glob('*.partial'))
    assert not list(manager.directory.glob('.export-*'))


def test_7z_payload_tampering_is_rejected_before_restore(manager, tmp_path):
    archive = manager.export_all(tmp_path/'all.7z')
    unpacked = tmp_path/'unpacked'
    with py7zr.SevenZipFile(archive) as source:
        source.extractall(unpacked)
    (unpacked/'spedi'/'document.txt').write_text('tampered')
    bad = tmp_path/'tampered.7z'
    with py7zr.SevenZipFile(bad, 'w') as output:
        for path in unpacked.rglob('*'):
            if path.is_file():
                output.write(path, path.relative_to(unpacked).as_posix())
    with pytest.raises(ValueError):
        manager.schedule_restore(bad)
    assert not manager.pending.exists()
    assert_value(manager, 'original')


def test_export_contains_scenarios_and_reports_without_recursive_backup(manager, tmp_path):
    for label in ('scenarios', 'reports'):
        root = tmp_path/label
        root.mkdir()
        (root/'file.txt').write_text(label)
        manager.sources[label] = root
    target = manager.sources['reports']/'all.7z'
    manager.export_all(target)
    manager.export_all(target)
    names = {f['name'] for f in manager.inspect(target, verify=True)['files']}
    assert names == {'spedi/data.sqlite', 'spedi/document.txt', 'dispatcher/data.sqlite',
                     'dispatcher/document.txt', 'scenarios/file.txt', 'reports/file.txt'}


def test_7z_failure_mid_restore_rolls_back_new_files_and_current_data(manager, tmp_path, monkeypatch):
    added = manager.sources['spedi']/'added.txt'
    added.write_text('archived')
    archive = manager.export_all(tmp_path/'all.7z')
    added.unlink()
    change(manager, 'current')
    manager.schedule_restore(archive)
    install = manager._install
    calls = []
    def fail_once(path):
        calls.append(path)
        install(path)
        if len(calls) == 1:
            raise OSError('synthetic install failure')
    monkeypatch.setattr(manager, '_install', fail_once)
    with pytest.raises(OSError, match='synthetic'):
        manager.apply_pending()
    assert_value(manager, 'current')
    assert not added.exists()


def test_7z_verification_hashes_payload_without_writing_or_reading_unpacked_files(manager, tmp_path, monkeypatch):
    from spedi.backup_archive import DirectoryArchive
    archive = manager.export_all(tmp_path/'all.7z')
    original = DirectoryArchive.open
    def manifest_only(self, name):
        filename = getattr(name, 'filename', name)
        assert filename == 'manifest.json', 'Verification must hash the decompression stream directly'
        return original(self, name)
    monkeypatch.setattr(DirectoryArchive, 'open', manifest_only)
    assert len(manager.inspect(archive, verify=True)['files']) == 4


def test_list_cache_contains_only_small_metadata_and_restore_always_reverifies(manager, monkeypatch):
    archive = manager.create()
    manager._metadata_cache.clear()
    calls = []
    inspect = manager.inspect
    def counted(path, **kwargs):
        calls.append(kwargs.get('verify', False))
        return inspect(path, **kwargs)
    monkeypatch.setattr(manager, 'inspect', counted)
    manager.list_backups()
    manager.list_backups()
    assert calls == [False]
    metadata = manager._metadata_cache[archive][1]
    assert set(metadata) == {'created_at', 'reason', 'documents'}
    manager.schedule_restore(archive)
    assert calls[-1] is True


def test_changed_archive_is_not_hidden_by_cached_metadata(manager):
    archive = manager.create()
    assert len(manager.list_backups()) == 1
    archive.write_bytes(b'changed archive contents')
    assert manager.list_backups() == []
