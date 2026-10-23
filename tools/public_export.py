"""Create and audit an explicit source-only publication; never copy user storage."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path

ROOT_FILES = {
    '.gitignore', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'README.pl.md',
    'pyproject.toml', 'build.ps1', 'Spedi.spec',
}
SOURCE_DIRS = {
    'spedi': {'.py', '.qss', '.ico', '.json', '.txt', '.pem', '.bin', ''},
    'brudnopis_ot': {'.py'},
    'ewidentor': {'.py'},
    'ddd_analyzer': {'.py', '.geojson'},
    'markus_dispatcher': {'.py', '.qss', '.ico', '.png'},
    'tests': {'.py'}, 'tools': {'.py'}, 'docs': {'.md'},
    'installer': {'.iss'},
    'mobile': {'.kt', '.kts', '.xml', '.properties', '.pro', '.bat', '.ps1', '.md', '.jar', ''},
    'firebase': {'.rules', '.json', '.ps1', '.md'},
    '.github': {'.yml', '.yaml'},
}
SKIP_DIRS = {'.git', '.venv', '.gradle', '__pycache__', '.pytest_cache', 'build',
             'dist', 'release', 'artifacts', 'work', 'node_modules', 'research',
             'dane', 'ddd_files', 'ewidencja', 'raporty', 'scenariusze', 'zalaczniki'}
PRIVATE_NAMES = {
    'google-services.json', 'local.properties', 'existing-rules.json',
    'config.json', 'settings.json', '.env',
}
PRIVATE_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.ddd', '.dpapi', '.keystore',
                    '.jks', '.p12', '.pfx', '.log', '.pdf', '.csv', '.xlsx', '.docx'}
CREDENTIAL_PATTERNS = [
    re.compile(rb'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----'),
    re.compile(rb'AIza[0-9A-Za-z_-]{30,}'),
    re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}'),
    re.compile(rb'ya29\.[A-Za-z0-9_-]{20,}'),
    re.compile(rb'1//[0-9A-Za-z_-]{40,}'),
    re.compile(rb'"private_key"\s*:\s*"[^"\r\n]{15,}'),
    re.compile(rb'"client_secret"\s*:\s*"[^"\r\n]{15,}'),
    re.compile(rb'"(?:access_token|refresh_token)"\s*:\s*"[^"\r\n]{15,}'),
]

# Only reviewed, generated showcase images may be published from docs/.
# Digests are filled after the explicit review; arbitrary screenshots stay out.
SHOWCASE_ASSETS: dict[str, str] = {'docs/showcase/assets/01-brudnopis.png': 'a1e2e130f8582e8cdc263ce5ac971779faf926e30d865183af95b419255cae34', 'docs/showcase/assets/02-lista-ot.png': '9e00d8af955100dac5e2d43713b440d17e8c5ef927cada198f4f8e28c27efca2', 'docs/showcase/assets/03-ddd.png': '77c4b271b1463b0ce0f240b4806fc361f309dbd28d3022f17f029449d37eba4f', 'docs/showcase/assets/04-ewidentor.png': 'b24d5e177f1423260527ffcf02afa48a7bb8fd7e7d08dd525b17ec8222bf14eb', 'docs/showcase/assets/04b-ewidentor-lista.png': 'cdfdbc273497dcbd6c76974986f860f4b396ac63b321d13eab2500b32d2537cb', 'docs/showcase/assets/05-dispatcher.png': '3e939f753cc4cb24f586d6d27e1b98cb969c344993b51a8d2497daf123e292f0', 'docs/showcase/assets/06-pod.png': 'a71c9d609affcb7ba5bf77901d3daddc91278ef2344f0c677a226d7242d85e5c', 'docs/showcase/assets/07-import.png': '6eddf55a461b5bdd7c31f2186e3d91dcdbdf53455183e0f37ad8e9f572c8f578', 'docs/showcase/assets/08-rozliczenie.png': '2c404e6e5c8889a5a00df43e8f1774ea2cae1223ca403eba8e744055a8227abe', 'docs/showcase/assets/import-demo.gif': '677badf3785f6f54d2d7e0de73180415bf62d9259fd4327921bf8059051e7eca'}


def ignored(parts: tuple[str, ...]) -> bool:
    return any(part in SKIP_DIRS or part.endswith('.egg-info') for part in parts)


def private_name(path: Path) -> bool:
    name = path.name.lower()
    return (name in PRIVATE_NAMES or path.suffix.lower() in PRIVATE_SUFFIXES
            or any(name.startswith(prefix) for prefix in ('credentials', 'client_secret', 'service-account', 'service_account', 'token.', '.env.'))
            or any(mark in name for mark in ('.sqlite-', '.sqlite3-', '.db-wal', '.db-shm')))


def selected_files(root: Path) -> list[Path]:
    result = []
    for name in sorted(ROOT_FILES):
        path = root / name
        if not path.is_file():
            raise ValueError(f'Missing publication file: {name}')
        result.append(path)
    for name, extensions in SOURCE_DIRS.items():
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob('*'):
            relative = path.relative_to(root)
            if path.is_symlink():
                raise ValueError(f'Symlink not allowed: {relative}')
            if not path.is_file() or ignored(relative.parts):
                continue
            approved_digest = SHOWCASE_ASSETS.get(relative.as_posix())
            if approved_digest:
                if hashlib.sha256(path.read_bytes()).hexdigest() != approved_digest:
                    raise ValueError(f'Showcase asset changed; review required: {relative.as_posix()}')
                result.append(path)
                continue
            if private_name(path) or path.suffix.lower() not in extensions:
                continue
            if path.suffix == '.json' and relative.as_posix() not in {'spedi/locales/en.json', 'firebase/firebase.json'}:
                continue
            if path.suffix in {'.pem', '.bin', '.txt'} and 'spedi/_vendor/tachograph/' not in relative.as_posix():
                continue
            if path.suffix == '.jar' and relative.as_posix() != 'mobile/gradle/wrapper/gradle-wrapper.jar':
                continue
            result.append(path)
    return sorted(set(result))


def audit(root: Path, files: list[Path] | None = None, forbidden_values: tuple[bytes, ...] = ()) -> list[str]:
    findings = []
    allowed = set(selected_files(root))
    if files is None:
        files = [p for p in root.rglob('*') if p.is_file() and not ignored(p.relative_to(root).parts)]
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or private_name(path) or path not in allowed:
            findings.append(f'Unexpected or private file: {relative}')
            continue
        contents = path.read_bytes()
        if any(pattern.search(contents) for pattern in CREDENTIAL_PATTERNS):
            findings.append(f'Credential pattern: {relative}')
        if any(value in contents for value in forbidden_values if len(value) >= 8):
            findings.append(f'Known private value: {relative}')
    return findings


def export(source: Path, destination: Path) -> tuple[int, str]:
    source = source.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Destination must be a new directory; existing files are never overwritten.')
    if destination == source or destination in source.parents:
        raise ValueError('Destination cannot contain the source directory.')
    files = selected_files(source)
    findings = audit(source, files)
    if findings:
        raise ValueError('\n'.join(findings))
    destination.mkdir(parents=True)
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        digest.update(relative.as_posix().encode('utf-8') + b'\0' + hashlib.sha256(target.read_bytes()).digest())
    return len(files), digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--output', type=Path)
    group.add_argument('--check', type=Path)
    args = parser.parse_args()
    if args.check:
        findings = audit(args.check.resolve())
        if findings:
            print('\n'.join(findings))
            return 1
        print('Publication file audit passed.')
    else:
        count, digest = export(args.source, args.output)
        print(f'Exported {count} source files. Manifest SHA256: {digest}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
