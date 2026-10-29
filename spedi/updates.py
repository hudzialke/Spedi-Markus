"""Read-only public release checks. No installer execution or account access."""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import time

from spedi import __version__
from spedi.backups import _write_json

PROJECT_URL = 'https://github.com/hudzialke/Spedi-Markus'
LATEST_URL = 'https://api.github.com/repos/hudzialke/Spedi-Markus/releases/latest'


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('Nieprawidłowy numer wersji wydania.')
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    release_url: str
    installer_url: str
    notes: str

    def newer_than(self, current=__version__):
        return version_tuple(self.version) > version_tuple(current)

    def validate(self):
        version_tuple(self.version)
        if (self.tag != 'v'+self.version or self.release_url != PROJECT_URL+'/releases/tag/'+self.tag
                or self.installer_url != PROJECT_URL+'/releases/download/'+self.tag+'/Spedi-Setup.exe'
                or not isinstance(self.notes, str) or len(self.notes) > 100_000):
            raise ValueError('Wydanie ma nieprawidłowe źródło pobierania.')
        return self


def public_release():
    import requests
    response = requests.get(LATEST_URL, headers={'Accept': 'application/vnd.github+json',
        'User-Agent': 'Spedi/'+__version__}, timeout=(3, 10), allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200 or len(response.content) > 1024**2:
        raise ValueError('Nie można odczytać aktualnego wydania.')
    return response.json()


class UpdateService:
    def __init__(self, path, *, fetch=public_release, now=time.time):
        self.path = Path(path)
        self.fetch = fetch
        self.now = now

    def _state(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def automatic(self):
        return self._state().get('automatic', True) is not False

    def set_automatic(self, enabled):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.path, {**self._state(), 'automatic': bool(enabled)})

    def due(self):
        checked = self._state().get('checked_at', 0)
        elapsed = self.now()-checked if isinstance(checked, (int, float)) else -1
        return self.automatic and (elapsed < 0 or elapsed >= 24*3600 or not self.cached())

    def cached(self):
        try:
            return ReleaseInfo(**self._state()['release']).validate()
        except (KeyError, TypeError, ValueError):
            return None

    def check(self):
        data = self.fetch()
        if data.get('draft') or data.get('prerelease'):
            raise ValueError('Wybrane wydanie nie jest stabilną wersją programu.')
        tag = data['tag_name']
        version_tuple(tag)
        version = tag.removeprefix('v')
        installer = next((item for item in data.get('assets', []) if item.get('name') == 'Spedi-Setup.exe'), None)
        if not installer:
            raise ValueError('Wydanie nie zawiera instalatora Windows.')
        info = ReleaseInfo(version, tag, data['html_url'], installer['browser_download_url'],
                           str(data.get('body') or '')[:100_000]).validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(self.path, {**self._state(), 'checked_at': self.now(), 'release': asdict(info)})
        return info
