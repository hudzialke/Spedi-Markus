"""Local interface translations. Never translate user values or storage keys."""
from __future__ import annotations

import json
import os
from pathlib import Path

from platformdirs import user_config_dir

LANGUAGES = {"pl": "Polski", "en": "English"}
_language: str | None = None
_catalog: dict[str, str] | None = None


def settings_path() -> Path:
    override = os.environ.get("SPEDI_SETTINGS_FILE")
    return Path(override) if override else Path(user_config_dir("Spedi", appauthor=False)) / "settings.json"


def language() -> str:
    global _language
    if _language is None:
        selected = os.environ.get("SPEDI_LANGUAGE")
        if selected is None:
            try:
                selected = json.loads(settings_path().read_text(encoding="utf-8")).get("language")
            except (OSError, ValueError, AttributeError):
                selected = None
        _language = selected if selected in LANGUAGES else "pl"
    return _language


def save_language(selected: str) -> None:
    if selected not in LANGUAGES:
        raise ValueError("Unsupported language")
    path = settings_path()
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings["language"] = selected
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def tr(source: str) -> str:
    global _catalog
    if language() == "pl":
        return source
    if _catalog is None:
        _catalog = json.loads((Path(__file__).parent / "locales" / "en.json").read_text(encoding="utf-8"))
    return _catalog.get(source, source)


def configure_qt(app) -> None:
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    locale = QLocale("en_GB" if language() == "en" else "pl_PL")
    QLocale.setDefault(locale)
    translator = QTranslator(app)
    if translator.load(locale, "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    app._spedi_qt_translator = translator
