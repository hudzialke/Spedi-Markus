"""Bounded archive readers shared by portable backup and restore."""
from contextlib import contextmanager
from spedi.i18n import tr
from pathlib import Path
import os
import hashlib
import shutil
import tempfile
from zipfile import ZipFile, ZipInfo


class DirectoryArchive:
    def __init__(self, root, infos=None):
        from spedi.backups import _safe_name, _MAX_BYTES, _walk_error
        self.root = Path(root)
        if self.root.is_symlink() or self.root.is_junction():
            raise ValueError(tr('Kopia nie obsługuje dowiązań.'))
        self.infos = {}
        total = 0
        if infos is None:
            infos = []
            for folder, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
                for name in dirs + files:
                    path = Path(folder)/name
                    if path.is_symlink() or path.is_junction():
                        raise ValueError(tr('Kopia nie obsługuje dowiązań.'))
                for name in files:
                    path = Path(folder)/name
                    info = ZipInfo(path.relative_to(root).as_posix())
                    info.file_size = path.stat().st_size
                    infos.append(info)
                    if len(infos) > 100_001:
                        raise ValueError(tr('Kopia przekracza limit liczby plików.'))
        for info in infos:
            _safe_name(info.filename)
            total += info.file_size
            if total > _MAX_BYTES or info.filename.casefold() in self.infos:
                raise ValueError(tr('Nieprawidłowa zawartość kopii.'))
            self.infos[info.filename.casefold()] = info

    def infolist(self):
        return list(self.infos.values())

    def getinfo(self, name):
        return self.infos[name.casefold()]

    def open(self, name):
        name = name.filename if isinstance(name, ZipInfo) else name
        self.getinfo(name)
        return (self.root/name).open('rb')

    def read(self, name):
        with self.open(name) as stream:
            return stream.read()


@contextmanager
def open_backup(path, *, extract=True, verify_only=False, cancelled=lambda: False):
    from spedi.backups import _safe_name, _MAX_BYTES
    path = Path(path)
    if path.is_dir():
        yield DirectoryArchive(path)
        return
    with path.open('rb') as stream:
        is_7z = stream.read(6) == b'7z\xbc\xaf\x27\x1c'
    if not is_7z:
        with ZipFile(path) as archive:
            yield archive
        return
    import py7zr
    from py7zr.io import Py7zIO, WriterFactory

    class Output(Py7zIO):
        def __init__(self, target, limit):
            self.stream = target.open('w+b') if target is not None else None
            self.limit = limit
            self.written = 0
            self.digest = hashlib.sha256()

        def write(self, data):
            if cancelled():
                raise InterruptedError(tr('Anulowano sprawdzanie kopii.'))
            self.written += len(data)
            if self.written > self.limit:
                raise ValueError(tr('Plik w kopii przekracza zadeklarowany rozmiar.'))
            self.digest.update(data)
            return self.stream.write(data) if self.stream is not None else len(data)

        def read(self, size=None):
            return self.stream.read(-1 if size is None else size) if self.stream is not None else b''

        def seek(self, offset, whence=0):
            return self.stream.seek(offset, whence) if self.stream is not None else 0

        def flush(self):
            if self.stream is not None:
                self.stream.flush()

        def size(self):
            return self.written

        def close(self):
            if self.stream is not None:
                self.stream.close()

    with tempfile.TemporaryDirectory(prefix='spedi-unpack-') as scratch:
        with py7zr.SevenZipFile(path, max_extract_size=_MAX_BYTES) as archive:
            infos = []
            seen = set()
            for item in archive.list():
                _safe_name(item.filename)
                if item.filename.casefold() in seen:
                    raise ValueError(tr('Kopia zawiera powtarzające się ścieżki.'))
                seen.add(item.filename.casefold())
                if len(seen) > 100_001 or (not item.is_directory and not item.is_file):
                    raise ValueError(tr('Kopia zawiera nieobsługiwane pliki.'))
                if item.is_directory:
                    continue
                info = ZipInfo(item.filename)
                info.file_size = item.uncompressed
                infos.append(info)
            view = DirectoryArchive(scratch, infos)
            if view.getinfo('manifest.json').file_size > 20*1024**2:
                raise ValueError(tr('Zbyt duży opis kopii.'))
            required = sum(i.file_size for i in infos) if extract and not verify_only else view.getinfo('manifest.json').file_size
            if shutil.disk_usage(scratch).free < required + 64*1024**2:
                raise OSError(tr('Za mało miejsca na rozpakowanie kopii.'))

            class Factory(WriterFactory):
                def __init__(self):
                    self.outputs = {}

                def create(self, filename):
                    info = view.getinfo(filename)
                    target = None
                    if not verify_only or filename == 'manifest.json':
                        target = Path(scratch)/_safe_name(filename)
                        target.parent.mkdir(parents=True, exist_ok=True)
                    output = Output(target, info.file_size)
                    self.outputs[filename] = output
                    return output

            factory = Factory()
            try:
                archive.extract(targets=None if extract else ['manifest.json'], factory=factory)
            finally:
                for output in factory.outputs.values():
                    output.close()
            view.hashes = {name: output.digest.hexdigest() for name, output in factory.outputs.items()}
        yield view
