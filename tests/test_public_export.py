from pathlib import Path
import hashlib

import pytest

from tools.public_export import ROOT_FILES, SHOWCASE_ASSETS, audit, export, selected_files


def source_tree(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    for name in ROOT_FILES:
        (root / name).write_text('public source', encoding='utf-8')
    return root


def test_export_excludes_personal_data_and_credentials(tmp_path):
    root = source_tree(tmp_path)
    for name in ['mobile/app/google-services.json', 'mobile/local.properties',
                 'firebase/existing-rules.json', 'spedi/service-account.json',
                 'spedi/client_secret.json', 'spedi/profiles.sqlite',
                 'spedi/driver.DDD', 'spedi/token.json', 'spedi/.env',
                 'spedi/dane/customer.json', 'spedi/report.pdf', 'work/debug.py']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private synthetic fixture', encoding='utf-8')
    (root / 'spedi/app.py').write_text('print("public")', encoding='utf-8')
    destination = tmp_path / 'public'
    export(root, destination)
    assert (destination / 'spedi/app.py').is_file()
    assert {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()} == ROOT_FILES | {'spedi/app.py'}
    assert audit(destination) == []


def test_export_rejects_embedded_key_and_does_not_print_it(tmp_path):
    root = source_tree(tmp_path)
    path = root / 'spedi/app.py'
    path.parent.mkdir()
    fake_key = 'AIza' + 'A' * 35
    path.write_text(f'KEY = "{fake_key}"', encoding='utf-8')
    findings = audit(root, selected_files(root))
    assert findings == ['Credential pattern: spedi/app.py']
    assert fake_key not in str(findings)
    with pytest.raises(ValueError, match='Credential pattern'):
        export(root, tmp_path / 'public')


def test_check_rejects_unexpected_file_and_export_never_overwrites(tmp_path):
    root = source_tree(tmp_path)
    (root / 'private.sqlite').write_bytes(b'synthetic database')
    assert audit(root) == ['Unexpected or private file: private.sqlite']
    with pytest.raises(ValueError, match='new directory'):
        export(root, root)


def test_public_verification_key_is_allowed_but_private_key_is_rejected(tmp_path):
    root = source_tree(tmp_path)
    path = root / 'spedi/_vendor/tachograph/certs/public.pem'
    path.parent.mkdir(parents=True)
    path.write_text('-----BEGIN PUBLIC KEY-----\nfixture\n-----END PUBLIC KEY-----')
    assert audit(root) == []
    path.write_text('-----BEGIN ' + 'PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----')
    assert audit(root) == ['Credential pattern: spedi/_vendor/tachograph/certs/public.pem']


def test_showcase_exports_only_reviewed_asset_and_excludes_working_data(tmp_path, monkeypatch):
    root = source_tree(tmp_path)
    relative = 'docs/showcase/assets/approved.png'
    image = root / relative
    image.parent.mkdir(parents=True)
    image.write_bytes(b'generated fictional image fixture')
    monkeypatch.setitem(SHOWCASE_ASSETS, relative, hashlib.sha256(image.read_bytes()).hexdigest())
    (image.parent / 'unreviewed.png').write_bytes(b'unreviewed screenshot fixture')
    (image.parent / 'demo.sqlite').write_bytes(b'working database fixture')
    destination = tmp_path / 'public'
    export(root, destination)
    assert (destination / relative).read_bytes() == image.read_bytes()
    assert not (destination / image.parent.relative_to(root) / 'unreviewed.png').exists()
    assert not (destination / image.parent.relative_to(root) / 'demo.sqlite').exists()
    assert audit(destination) == []


def test_replacing_reviewed_showcase_image_requires_new_review(tmp_path, monkeypatch):
    root = source_tree(tmp_path)
    relative = 'docs/showcase/assets/approved.png'
    image = root / relative
    image.parent.mkdir(parents=True)
    image.write_bytes(b'generated fictional image fixture')
    monkeypatch.setitem(SHOWCASE_ASSETS, relative, hashlib.sha256(image.read_bytes()).hexdigest())
    image.write_bytes(b'replacement screenshot fixture')
    destination = tmp_path / 'public'
    with pytest.raises(ValueError, match='Showcase asset changed; review required'):
        export(root, destination)
    assert not destination.exists()
