from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from platformdirs import user_data_dir


class HudonizatorDbError(Exception):
    """Błąd odczytu bazy Hudonizatora (komunikat po polsku)."""


@dataclass(frozen=True, slots=True)
class DriverRef:
    id: int
    display_name: str
    card_number: str | None
    date_from: date | None = None
    date_to: date | None = None
    segment_count: int = 0


@dataclass(frozen=True, slots=True)
class RawSegment:
    day: date
    activity: str
    start_minute: int
    end_minute: int


def default_db_path() -> Path:
    """Ta sama lokalizacja co Hudonizator (platformdirs)."""
    return Path(user_data_dir("Hudonizator", "Spedi")) / "hudonizator.db"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise HudonizatorDbError(
            "Brak bazy Hudonizatora.\n"
            f"Oczekiwany plik:\n{db_path}\n\n"
            "Najpierw zaimportuj pliki .ddd w Hudonizatorze."
        )
    # mode=ro avoids accidental writes; URI required for query params
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def list_drivers(db_path: Path | None = None) -> list[DriverRef]:
    path = db_path or default_db_path()
    try:
        conn = _connect_ro(path)
    except HudonizatorDbError:
        raise
    except sqlite3.Error as exc:
        raise HudonizatorDbError(f"Nie można otworzyć bazy Hudonizatora:\n{exc}") from exc

    try:
        rows = conn.execute(
            """
            SELECT
                d.id AS id,
                d.display_name AS display_name,
                d.card_number AS card_number,
                MIN(s.day) AS date_from,
                MAX(s.day) AS date_to,
                COUNT(s.id) AS segment_count
            FROM drivers d
            LEFT JOIN activity_segments s ON s.driver_id = d.id
            GROUP BY d.id, d.display_name, d.card_number
            ORDER BY lower(d.display_name) ASC, d.id ASC
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise HudonizatorDbError(
            f"Baza Hudonizatora jest uszkodzona lub w nieoczekiwanym formacie:\n{exc}"
        ) from exc
    finally:
        conn.close()

    result: list[DriverRef] = []
    for row in rows:
        result.append(
            DriverRef(
                id=int(row["id"]),
                display_name=str(row["display_name"] or "Bez nazwy"),
                card_number=str(row["card_number"]) if row["card_number"] else None,
                date_from=_parse_date(row["date_from"]),
                date_to=_parse_date(row["date_to"]),
                segment_count=int(row["segment_count"] or 0),
            )
        )
    return result


def load_segments(driver_id: int, db_path: Path | None = None) -> list[RawSegment]:
    path = db_path or default_db_path()
    try:
        conn = _connect_ro(path)
    except HudonizatorDbError:
        raise
    except sqlite3.Error as exc:
        raise HudonizatorDbError(f"Nie można otworzyć bazy Hudonizatora:\n{exc}") from exc

    try:
        rows = conn.execute(
            """
            SELECT day, activity, start_minute, end_minute
            FROM activity_segments
            WHERE driver_id = ?
            ORDER BY day ASC, start_minute ASC
            """,
            (driver_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise HudonizatorDbError(f"Nie udało się wczytać segmentów:\n{exc}") from exc
    finally:
        conn.close()

    segments: list[RawSegment] = []
    for row in rows:
        day = _parse_date(row["day"])
        if day is None:
            continue
        segments.append(
            RawSegment(
                day=day,
                activity=str(row["activity"] or ""),
                start_minute=int(row["start_minute"]),
                end_minute=int(row["end_minute"]),
            )
        )
    return segments
