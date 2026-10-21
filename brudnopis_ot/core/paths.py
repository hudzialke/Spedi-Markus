from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def app_root() -> Path:
    """Katalog aplikacji: obok EXE (frozen) albo katalog projektu (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # brudnopis_ot/core/paths.py -> brudnopis-ot/
    return Path(__file__).resolve().parents[2]


def reports_dir() -> Path:
    """Folder raportów PDF — tworzony przy pierwszym użyciu."""
    env = os.environ.get("SPEDI_DATA_DIR", "").strip()
    path = Path(env).parent / "raporty" if env else app_root() / "raporty"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scenarios_dir() -> Path:
    """Domyślny katalog zapisu scenariuszy JSON."""
    env = os.environ.get("SPEDI_DATA_DIR", "").strip()
    path = Path(env).parent / "scenariusze" if env else app_root() / "scenariusze"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    """Katalog danych programu (baza DDD itd.)."""
    env = os.environ.get("SPEDI_DATA_DIR", "").strip()
    if env:
        path = Path(env)
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = app_root() / "dane"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename_part(text: str, *, max_len: int = 40) -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", (text or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("_") or "raport"
    return cleaned[:max_len]
