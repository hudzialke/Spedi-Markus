from __future__ import annotations

import os
import shutil
from pathlib import Path

from spedi.paths import app_root, brudnopis_db, data_dir, ewidentor_db


def local_app_data() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def _copy_if_missing(src: Path, dest: Path) -> bool:
    if dest.exists() or not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def migrate_legacy_databases() -> list[str]:
    """Skopiuj stare bazy Brudnopisu / Ewidentora, jeśli Spedi jeszcze ich nie ma."""
    notes: list[str] = []
    home = Path.home()
    local = local_app_data()
    installed_data = app_root() / "dane"
    previous_user_data = local / "Spedi" / "Spedi" / "dane"
    brudnopis_candidates = [
        installed_data / "ddd_profiles.sqlite",
        previous_user_data / "ddd_profiles.sqlite",
        home / "brudnopis-ot" / "release" / "BrudnopisOT" / "dane" / "ddd_profiles.sqlite",
        home / "brudnopis-ot" / "dane" / "ddd_profiles.sqlite",
        local / "Programs" / "BrudnopisOT" / "dane" / "ddd_profiles.sqlite",
    ]
    ewidentor_candidates = [
        installed_data / "ewidentor.sqlite",
        previous_user_data / "ewidentor.sqlite",
        home / "ewidentor" / "release" / "Ewidentor" / "dane" / "ewidentor.sqlite",
        home / "ewidentor" / "dane" / "ewidentor.sqlite",
        local / "Programs" / "Ewidentor" / "dane" / "ewidentor.sqlite",
    ]
    dest_b = brudnopis_db()
    for src in brudnopis_candidates:
        if _copy_if_missing(src, dest_b):
            notes.append(f"Przeniesiono bazę Brudnopisu z {src}")
            break
    dest_e = ewidentor_db()
    for src in ewidentor_candidates:
        if _copy_if_missing(src, dest_e):
            notes.append(f"Przeniesiono bazę Ewidentora z {src}")
            break
    data_dir()
    return notes
