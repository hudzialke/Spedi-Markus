from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ddd_analyzer.adapter import parse_tacho_result
from ddd_analyzer.models import DddAnalysis, DddFileKind


def _default_parser_root() -> Path:
    env = os.environ.get("DDD_PARSER_ROOT")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    from spedi import __file__ as spedi_file
    return Path(spedi_file).resolve().parent / "_vendor" / "tachograph"


def ensure_parser_path(parser_root: Path | None = None) -> Path:
    root = (parser_root or _default_parser_root()).resolve()
    if getattr(sys, "frozen", False):
        root_str = str(root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        return root
    if not root.joinpath("app", "engine.py").is_file():
        raise FileNotFoundError(
            "Nie znaleziono parsera ddd-tachograph-reader. "
            f"Oczekiwana sciezka: {root}. "
            "Pobierz repozytorium Syax89/ddd-tachograph-reader obok folderu ddd-analyzer "
            "lub ustaw zmienna DDD_PARSER_ROOT."
        )
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


def detect_file_kind(path: Path, is_vu: bool | None = None) -> DddFileKind:
    name = path.name.upper()
    if is_vu is True or name.startswith("M_"):
        return DddFileKind.VU
    if is_vu is False or name.startswith("C_"):
        return DddFileKind.CARD
    return DddFileKind.UNKNOWN


def parse_ddd_file(path: str | Path, parser_root: Path | None = None) -> DddAnalysis:
    ensure_parser_path(parser_root)
    from app.engine import TachoParser

    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Plik nie istnieje: {file_path}")

    parser = TachoParser(str(file_path))
    raw = parser.parse()
    meta = raw.get("metadata") or {}
    parse_error = meta.get("parse_error")
    if parse_error:
        message = parse_error.get("message", parse_error) if isinstance(parse_error, dict) else str(parse_error)
        raise ValueError(f"Blad parsowania pliku DDD: {message}")

    kind = detect_file_kind(file_path, meta.get("is_vu"))
    analysis = parse_tacho_result(raw, str(file_path), kind)
    analysis.read_at = datetime.now(timezone.utc).astimezone()
    # read_at describes analysis time only; it is not evidence of DDD coverage.
    return analysis
