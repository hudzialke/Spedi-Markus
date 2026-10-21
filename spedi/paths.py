from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Spedi"
APP_AUTHOR = "Spedi"
ENV_DATA_DIR = "SPEDI_DATA_DIR"


def app_root() -> Path:
    """Katalog instalacji: obok EXE (frozen) albo root repo (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _override_dir() -> Path | None:
    raw = os.environ.get(ENV_DATA_DIR, "").strip()
    if raw:
        path = Path(raw)
        path.mkdir(parents=True, exist_ok=True)
        return path
    return None


def data_dir() -> Path:
    """Zapisywalny katalog %LOCALAPPDATA%\\Spedi\\dane (lub odpowiednik systemowy)."""
    override = _override_dir()
    if override is not None:
        return override
    # Program Files is read-only for a regular user. Keep all mutable data in
    # the user profile even when the application is installed there.
    path = Path(user_data_dir(APP_NAME, appauthor=False)) / "dane"
    path.mkdir(parents=True, exist_ok=True)
    return path


def brudnopis_db() -> Path:
    return data_dir() / "ddd_profiles.sqlite"


def ewidentor_db() -> Path:
    return data_dir() / "ewidentor.sqlite"


def ddd_files_dir() -> Path:
    path = data_dir() / "ddd_files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_module_paths() -> None:
    """Ustaw SPEDI_DATA_DIR zanim zaimportują się magazyny Brudnopisu / Ewidentora."""
    os.environ.setdefault(ENV_DATA_DIR, str(data_dir()))
