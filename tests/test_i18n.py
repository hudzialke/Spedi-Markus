import json
import ast
from pathlib import Path

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


def test_english_catalog_covers_all_explicit_interface_messages():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root/'spedi/locales/en.json').read_text(encoding='utf-8'))
    missing = set()
    for package in ('spedi', 'brudnopis_ot', 'ewidentor', 'markus_dispatcher', 'ddd_analyzer'):
        for path in (root/package).rglob('*.py'):
            if '_vendor' in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in {'tr', '_tr'} and node.args
                        and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    text = node.args[0].value
                    if not catalog.get(text):
                        missing.add(text)
    assert not missing, f'Missing English translations: {sorted(missing)}'


def test_backup_errors_and_backup_types_follow_english_selection(isolated_settings, monkeypatch, tmp_path):
    monkeypatch.setenv('SPEDI_LANGUAGE', 'en')
    from spedi.backups import BackupManager
    manager = BackupManager({'spedi': tmp_path/'data'}, tmp_path/'backups')
    with pytest.raises(ValueError, match='filename ending in .7z'):
        manager.export_all(tmp_path/'invalid.zip')
    assert i18n.tr('Ręczna') == 'Manual'
    assert i18n.tr('Przed przywróceniem') == 'Before restore'


def test_english_findings_and_guidance_keep_the_same_rule_results(isolated_settings, monkeypatch):
    from datetime import date
    from brudnopis_ot.core.models import Scenario
    from brudnopis_ot.core.validator import validate_scenario
    scenario = Scenario(week_start=date(2026, 11, 23), weeks=1)
    polish = validate_scenario(scenario)
    monkeypatch.setattr(i18n, '_language', 'en')
    english = validate_scenario(scenario)
    assert [(f.code, f.severity, f.underline_range) for f in english.findings] == [
        (f.code, f.severity, f.underline_range) for f in polish.findings]
    assert english.findings[0].message_pl == 'No weekly-rest violations in this scenario.'
    assert any('Week 1:' in hint and 'remaining' in hint for hint in english.compensation_hints)


def test_translation_placeholders_are_preserved():
    import string
    catalog = json.loads((Path(__file__).resolve().parents[1]/'spedi/locales/en.json').read_text(encoding='utf-8'))
    formatter = string.Formatter()
    for source, english in catalog.items():
        if '{v0}' in source or source.startswith((' Start tygodnia 1:', ' OT obejmujący granicę', ' Plan od powrotu:')):
            original = {name for _, name, _, _ in formatter.parse(source) if name is not None}
            translated = {name for _, name, _, _ in formatter.parse(english) if name is not None}
            assert original == translated, source
