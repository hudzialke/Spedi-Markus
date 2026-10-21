from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    env = os.environ.get("SPEDI_DATA_DIR", "").strip()
    if env:
        path = Path(env)
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = app_root() / "dane"
    path.mkdir(parents=True, exist_ok=True)
    return path


def reports_dir() -> Path:
    env = os.environ.get("SPEDI_DATA_DIR", "").strip()
    path = Path(env).parent / "raporty" if env else app_root() / "raporty"
    path.mkdir(parents=True, exist_ok=True)
    return path


def driver_reports_dir(driver_name: str) -> Path:
    """Osobny folder raportów dla kierowcy: raporty/<nazwa>/."""
    path = reports_dir() / safe_filename_part(driver_name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def attachments_dir() -> Path:
    path = data_dir() / "zalaczniki"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_brudnopis_db() -> Path:
    """Szuka bazy Brudnopisu — Spedi dane/, potem układ portable / dev."""
    root = app_root()
    env = os.environ.get("SPEDI_DATA_DIR", "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env) / "ddd_profiles.sqlite")
    candidates.extend(
        [
            data_dir() / "ddd_profiles.sqlite",
            root / "dane" / "ddd_profiles.sqlite",
            root.parent / "BrudnopisOT" / "dane" / "ddd_profiles.sqlite",
            root.parent / "brudnopis-ot" / "release" / "BrudnopisOT" / "dane" / "ddd_profiles.sqlite",
            root.parent / "brudnopis-ot" / "dane" / "ddd_profiles.sqlite",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def safe_filename_part(text: str, *, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", (text or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("_") or "raport"
    return cleaned[:max_len]
