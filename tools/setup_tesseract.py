"""Install a private portable OCR runtime; downloads software/models only, no documents."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '5.5.3.20260724'
INSTALLER_SHA256 = 'bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4'
URL = f'https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-{VERSION}.exe'
LANGUAGES = ('pol', 'eng', 'deu', 'fra', 'ita')
MODELS_REVISION = '87416418657359cb625c412a48b6e1d6d41c29bd'
MODEL_HASHES = {
    'pol': 'c4476cdbc0e33d898d32345122b7be1cbf85ace15f920f06c7714756e1ef79b2',
    'eng': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
    'deu': '19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d',
    'fra': 'ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca',
    'ita': 'b8f89e1e785118dac4d51ae042c029a64edb5c3ee42ef73027a6d412748d8827',
}


def download(url: str, path: Path):
    request = urllib.request.Request(url, headers={'User-Agent': 'Spedi-local-OCR-setup'})
    with urllib.request.urlopen(request, timeout=60) as response, path.open('wb') as output:
        shutil.copyfileobj(response, output)


def main():
    staging = ROOT/'work'/'ocr-setup'
    runtime = ROOT/'work'/'local-tools'/'tesseract'
    staging.mkdir(parents=True, exist_ok=True)
    installer = staging/'tesseract-5.5.3.exe'
    if not installer.is_file() or hashlib.sha256(installer.read_bytes()).hexdigest() != INSTALLER_SHA256:
        download(URL, installer)
    if hashlib.sha256(installer.read_bytes()).hexdigest() != INSTALLER_SHA256:
        raise RuntimeError('Nieprawidłowa suma kontrolna pobranego Tesseracta.')
    seven_zip = shutil.which('7z') or str(Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'7-Zip'/'7z.exe')
    if not Path(seven_zip).is_file():
        raise RuntimeError('Do rozpakowania prywatnej wersji Tesseract potrzebny jest lokalny 7-Zip.')
    runtime.mkdir(parents=True, exist_ok=True)
    subprocess.run([seven_zip, 'x', str(installer), '-o'+str(runtime), '-y',
                    'tesseract.exe', '*.dll', 'tessdata/configs/*', 'doc/*', '-x!$PLUGINSDIR/*'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    revision = MODELS_REVISION
    data_dir = runtime/'tessdata'
    data_dir.mkdir(exist_ok=True)
    model_hashes = {}
    for language in LANGUAGES:
        path = data_dir/f'{language}.traineddata'
        download(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{revision}/{path.name}', path)
        model_hashes[language] = hashlib.sha256(path.read_bytes()).hexdigest()
        if model_hashes[language] != MODEL_HASHES[language]:
            raise RuntimeError('Nieprawidłowa suma kontrolna danych językowych: ' + language)
        print('Pobrano dane językowe:', language)
    download(f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{revision}/LICENSE', data_dir/'LICENSE')
    manifest = {'version': VERSION, 'source': URL, 'installer_sha256': INSTALLER_SHA256,
                'models_source': 'tesseract-ocr/tessdata_fast', 'models_revision':revision, 'models_sha256':model_hashes}
    (runtime/'spedi-runtime.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Gotowy lokalny runtime:', runtime)


if __name__ == '__main__':
    main()
