import json

import pytest

from spedi import i18n


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    path = tmp_path / 'settings.json'
    monkeypatch.setenv('SPEDI_SETTINGS_FILE', str(path))
    monkeypatch.delenv('SPEDI_LANGUAGE', raising=False)
    monkeypatch.setattr(i18n, '_language', None)
    monkeypatch.setattr(i18n, '_catalog', None)
    return path


def test_language_persists_for_next_launch_and_preserves_other_settings(isolated_settings):
    isolated_settings.write_text(json.dumps({'other': 5}))
    assert i18n.language() == 'pl'
    i18n.save_language('en')
    assert i18n.language() == 'pl'  # Existing windows keep their language until restart.
    assert json.loads(isolated_settings.read_text()) == {'other': 5, 'language': 'en'}
    i18n._language = None
    assert i18n.language() == 'en'
    assert i18n.tr('Zapisz') == 'Save'
    assert i18n.tr('User-entered driver name') == 'User-entered driver name'


def test_invalid_or_corrupted_settings_fall_back_to_polish(isolated_settings):
    isolated_settings.write_text('{broken')
    assert i18n.language() == 'pl'
    assert i18n.tr('Zapisz') == 'Zapisz'
    with pytest.raises(ValueError):
        i18n.save_language('de')


def test_environment_can_select_english_without_writing_settings(isolated_settings, monkeypatch):
    monkeypatch.setenv('SPEDI_LANGUAGE', 'en')
    assert i18n.language() == 'en'
    assert i18n.tr('Kierowca') == 'Driver'
    assert i18n.tr('O programie') == 'About'
    assert not isolated_settings.exists()
