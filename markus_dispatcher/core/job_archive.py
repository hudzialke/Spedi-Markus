from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from markus_dispatcher import config
from markus_dispatcher.core.instruction import build_instruction
from markus_dispatcher.core.models import Driver, JobData

ALLOWED_FILE_SUFFIXES = {".pdf", ".xlsx", ".xls", ".xlsm", ".csv"}


@dataclass(frozen=True)
class ArchivePaths:
    driver_dir: Path
    job_dir: Path
    documents_dir: Path
    relative: str  # względem ARCHIVE_ROOT, ze slashami /


def slugify(text: str, *, max_len: int = 48) -> str:
    raw = text.strip().casefold()
    raw = (
        raw.replace("ą", "a")
        .replace("ć", "c")
        .replace("ę", "e")
        .replace("ł", "l")
        .replace("ń", "n")
        .replace("ó", "o")
        .replace("ś", "s")
        .replace("ź", "z")
        .replace("ż", "z")
    )
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = raw.strip("_") or "pozycja"
    return raw[:max_len]


def parse_job_stamp(job: JobData) -> datetime:
    """Data chronologii: załadunek, inaczej teraz."""
    text = (job.load_datetime or "").strip()
    for fmt in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.now()


def driver_folder_name(driver: Driver) -> str:
    return f"{driver.id:03d}_{slugify(driver.display_name)}"


def job_folder_name(job: JobData) -> str:
    assert job.id is not None
    stamp = parse_job_stamp(job).strftime("%Y-%m-%d_%H%M%S")
    ref = slugify(job.ref_numbers or f"job{job.id}", max_len=32)
    return f"{stamp}_{ref}_j{job.id}"


def resolve_archive_paths(driver: Driver, job: JobData) -> ArchivePaths:
    if job.id is None:
        raise ValueError("Zlecenie musi być zapisane przed ewidencją.")
    root = config.ARCHIVE_ROOT
    if job.archive_path:
        job_dir = root / Path(job.archive_path)
        # Gdy zmieniono kierowcę, archive_path może wskazywać stary folder — OK, zostawiamy.
        driver_dir = job_dir.parent
        relative = job.archive_path.replace("\\", "/")
    else:
        driver_dir = root / driver_folder_name(driver)
        job_dir = driver_dir / job_folder_name(job)
        relative = f"{driver_folder_name(driver)}/{job_folder_name(job)}"
    documents = job_dir / "dokumenty"
    return ArchivePaths(
        driver_dir=driver_dir,
        job_dir=job_dir,
        documents_dir=documents,
        relative=relative,
    )


def ensure_job_archive(
    driver: Driver,
    job: JobData,
    *,
    instruction_footer: str | None = None,
) -> ArchivePaths:
    """Tworzy folder zlecenia u kierowcy i odświeża zlecenie.txt + meta.json."""
    paths = resolve_archive_paths(driver, job)
    paths.documents_dir.mkdir(parents=True, exist_ok=True)
    instruction = build_instruction(job, footer=instruction_footer)
    (paths.job_dir / "zlecenie.txt").write_text(instruction, encoding="utf-8")
    meta = {
        "job_id": job.id,
        "driver_id": driver.id,
        "driver_name": driver.display_name,
        "ref": job.ref_numbers,
        "status": job.status,
        "client": job.client_name,
        "load_datetime": job.load_datetime,
        "unload_datetime": job.unload_datetime,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (paths.job_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return paths


def next_document_index(documents_dir: Path) -> int:
    docs_dir = documents_dir
    if not docs_dir.exists():
        return 1
    max_n = 0
    for path in docs_dir.iterdir():
        m = re.match(r"^(\d+)_", path.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def copy_file_attachment(
    documents_dir: Path,
    source: Path,
    *,
    label: str = "",
) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"Brak pliku: {source}")
    suffix = source.suffix.lower()
    if suffix not in ALLOWED_FILE_SUFFIXES:
        raise ValueError(
            "Dozwolone: PDF oraz Excel/CSV (.pdf, .xlsx, .xls, .xlsm, .csv)."
        )
    idx = next_document_index(documents_dir)
    base = slugify(label or source.stem, max_len=40)
    dest = documents_dir / f"{idx:02d}_{base}{suffix}"
    documents_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return dest


def write_sheets_shortcut(
    documents_dir: Path,
    url: str,
    *,
    label: str = "google_sheets",
) -> Path:
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Podaj pełny link http(s) do arkusza Google.")
    host = parsed.netloc.lower()
    if "docs.google.com" not in host and "sheets.google.com" not in host:
        # dopuszczamy też skrócone / inne domeny Google
        if "google." not in host:
            raise ValueError("Link powinien prowadzić do Google Sheets / Docs.")
    idx = next_document_index(documents_dir)
    base = slugify(label, max_len=40)
    dest = documents_dir / f"{idx:02d}_{base}.url"
    documents_dir.mkdir(parents=True, exist_ok=True)
    content = "[InternetShortcut]\nURL={}\n".format(cleaned)
    dest.write_text(content, encoding="utf-8")
    return dest


def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    name = path.stem.casefold()
    if suffix == ".pdf" and "pod" in name.split("_"):
        return "pod"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".url":
        return "sheets"
    if suffix in {".xlsx", ".xls", ".xlsm", ".csv"}:
        return "excel"
    return "file"


def list_document_files(documents_dir: Path) -> list[Path]:
    if not documents_dir.exists():
        return []
    files = [p for p in documents_dir.iterdir() if p.is_file()]
    return sorted(files, key=lambda p: p.name.casefold())
