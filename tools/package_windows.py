"""Package only the fresh PyInstaller bundle, never an existing installation."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spedi import __version__


def main():
    bundle = ROOT / 'dist' / 'Spedi'
    if not (bundle / 'Spedi.exe').is_file():
        raise RuntimeError('Build the application first.')
    # User storage must never be part of the portable download.
    files = sorted(path for path in bundle.rglob('*') if path.is_file())
    for path in files:
        relative = path.relative_to(bundle)
        if (set(relative.parts) & {'dane', 'ewidencja', 'raporty', 'scenariusze', 'ddd_files'}
                or path.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.ddd', '.dpapi', '.log'}
                or path.name.lower() in {'service-account.json', 'google-services.json', 'settings.json', 'config.json'}):
            raise RuntimeError('User storage or service configuration found in bundle.')
    output = ROOT / 'artifacts'
    output.mkdir(exist_ok=True)
    archive = output / f'Spedi-{__version__}-Windows-x64.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in files:
            zipped.write(path, 'Spedi/' + path.relative_to(bundle).as_posix())
    sums = []
    for path in (output / 'Spedi-Setup.exe', archive):
        if path.is_file():
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            sums.append(f'{digest}  {path.name}\n')
    (output / 'SHA256SUMS.txt').write_text(''.join(sums), encoding='utf-8')
    print('Packaged:', archive.name)


if __name__ == '__main__':
    main()
