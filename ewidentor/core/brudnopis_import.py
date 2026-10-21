from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ewidentor.core.models import Driver, PaySettings
from ewidentor.core.store import EwidentorStore


@dataclass(slots=True)
class BrudnopisProfile:
    profile_key: str
    source_kind: str
    driver_name: str
    card_number: str | None
    vehicle_plate: str | None
    date_from: date | None
    date_to: date | None
    title: str = ""


@dataclass(slots=True)
class RawSegment:
    day: date
    activity: str
    start_minute: int
    end_minute: int


@dataclass(slots=True)
class BorderCrossingRow:
    occurred_at: datetime
    country_left: str
    country_entered: str
    odometer_km: int | None = None
    source: str = "card"


@dataclass(slots=True)
class PlaceRow:
    """START/END miejsca pracy dziennej z Brudnopisu (ISO-2)."""

    occurred_at: datetime
    entry_type: str
    country_code: str
    odometer_km: int | None = None


@dataclass(slots=True)
class SyncResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    messages: list[str] | None = None

    def __post_init__(self) -> None:
        if self.messages is None:
            self.messages = []


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or text.upper() in {"N/A", "NONE", "-"}:
        return None
    return text


def resolve_card_number(
    *,
    card_number: str | None,
    profile_key: str,
) -> str | None:
    """Numer karty z pola DDD/Brudnopis albo z klucza profilu card:…"""
    direct = _clean(card_number)
    if direct:
        return direct
    key = (profile_key or "").strip()
    if key.lower().startswith("card:"):
        return _clean(key[5:])
    return None


def list_brudnopis_profiles(db_path: Path) -> list[BrudnopisProfile]:
    if not db_path.is_file():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(profiles)").fetchall()
        }
        select = [
            "profile_key",
            "driver_name",
            "card_number",
            "vehicle_plate",
            "date_from",
            "date_to",
        ]
        if "source_kind" in cols:
            select.insert(1, "source_kind")
        if "title" in cols:
            select.append("title")
        rows = conn.execute(
            f"SELECT {', '.join(select)} FROM profiles "
            "ORDER BY driver_name COLLATE NOCASE"
        ).fetchall()
    out: list[BrudnopisProfile] = []
    for r in rows:
        key = str(r["profile_key"])
        kind = str(r["source_kind"]) if "source_kind" in r.keys() else (
            "vu" if key.startswith("vu:") else "card"
        )
        card = resolve_card_number(
            card_number=r["card_number"],
            profile_key=key,
        )
        out.append(
            BrudnopisProfile(
                profile_key=key,
                source_kind=kind,
                driver_name=str(r["driver_name"] or ""),
                card_number=card,
                vehicle_plate=_clean(r["vehicle_plate"]),
                date_from=date.fromisoformat(r["date_from"]) if r["date_from"] else None,
                date_to=date.fromisoformat(r["date_to"]) if r["date_to"] else None,
                title=str(r["title"]) if "title" in r.keys() else "",
            )
        )
    return out


def load_month_segments(
    db_path: Path, profile_key: str, year: int, month: int
) -> list[RawSegment]:
    if not db_path.is_file():
        return []
    prefix = f"{year:04d}-{month:02d}-"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT day, activity, start_minute, end_minute FROM segments "
            "WHERE profile_key = ? AND day LIKE ? "
            "ORDER BY day, start_minute, end_minute",
            (profile_key, prefix + "%"),
        ).fetchall()
    return [
        RawSegment(
            day=date.fromisoformat(r["day"]),
            activity=str(r["activity"]),
            start_minute=int(r["start_minute"]),
            end_minute=int(r["end_minute"]),
        )
        for r in rows
    ]


def load_month_border_crossings(
    db_path: Path, profile_key: str, year: int, month: int
) -> list[BorderCrossingRow]:
    """Odczyt crossings z Brudnopisu (tabela może jeszcze nie istnieć)."""
    if not db_path.is_file():
        return []
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    # lekki bufor: crossing dzień przed/po miesiącu wpływają na przedziały
    from datetime import timedelta

    lo = (start - timedelta(days=2)).isoformat()
    hi = (end + timedelta(days=2)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT occurred_at, country_left, country_entered, odometer_km,
                       COALESCE(source, 'card') AS source
                FROM border_crossings
                WHERE profile_key = ?
                  AND occurred_at >= ? AND occurred_at < ?
                ORDER BY occurred_at
                """,
                (profile_key, lo, hi),
            ).fetchall()
        except sqlite3.OperationalError:
            try:
                rows = conn.execute(
                    """
                    SELECT occurred_at, country_left, country_entered, odometer_km
                    FROM border_crossings
                    WHERE profile_key = ?
                      AND occurred_at >= ? AND occurred_at < ?
                    ORDER BY occurred_at
                    """,
                    (profile_key, lo, hi),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
    out: list[BorderCrossingRow] = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r["occurred_at"]))
        except ValueError:
            continue
        source = "card"
        if "source" in r.keys():
            source = str(r["source"] or "card")
        out.append(
            BorderCrossingRow(
                occurred_at=ts,
                country_left=str(r["country_left"] or ""),
                country_entered=str(r["country_entered"] or ""),
                odometer_km=int(r["odometer_km"])
                if r["odometer_km"] is not None
                else None,
                source=source,
            )
        )
    return out


def load_month_places(
    db_path: Path, profile_key: str, year: int, month: int
) -> list[PlaceRow]:
    """Odczyt places (START/END) z Brudnopisu — źródło krajów gdy brak crossings."""
    if not db_path.is_file():
        return []
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    from datetime import timedelta

    lo = (start - timedelta(days=2)).isoformat()
    hi = (end + timedelta(days=2)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT occurred_at, entry_type, country_code, odometer_km
                FROM places
                WHERE profile_key = ?
                  AND occurred_at >= ? AND occurred_at < ?
                ORDER BY occurred_at
                """,
                (profile_key, lo, hi),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    out: list[PlaceRow] = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r["occurred_at"]))
        except ValueError:
            continue
        code = str(r["country_code"] or "").strip().upper()
        if not code:
            continue
        out.append(
            PlaceRow(
                occurred_at=ts,
                entry_type=str(r["entry_type"] or "").strip().upper(),
                country_code=code,
                odometer_km=int(r["odometer_km"])
                if r["odometer_km"] is not None
                else None,
            )
        )
    return out


def profile_to_driver_draft(profile: BrudnopisProfile) -> Driver | None:
    """Mapuj profil Brudnopisu na draft kierowcy. VU bez karty — pomijamy."""
    if profile.source_kind == "vu" and not profile.card_number:
        return None
    name = (profile.driver_name or "").strip()
    if not name or name == "Kierowca":
        if profile.card_number:
            name = f"Kierowca {profile.card_number[-6:]}"
        else:
            name = profile.profile_key
    return Driver(
        id=None,
        full_name=name,
        card_number=profile.card_number,
        brudnopis_profile_key=profile.profile_key,
        notes=(
            f"Zaimportowano z Brudnopisu ({profile.source_kind})"
            + (f"; VU {profile.vehicle_plate}" if profile.vehicle_plate else "")
        ),
        pay=PaySettings(),
    )


def sync_drivers_from_brudnopis(
    store: EwidentorStore,
    db_path: Path,
    *,
    only_card: bool = True,
) -> SyncResult:
    """
    Zaciągnij profile z bazy Brudnopisu do Ewidentora.
    Istniejące: aktualizuje nazwę / kartę / klucz (nie nadpisuje stawek).
    """
    result = SyncResult()
    profiles = list_brudnopis_profiles(db_path)
    if not profiles:
        result.messages.append("Brak profili w bazie Brudnopisu.")
        return result

    for profile in profiles:
        if only_card and profile.source_kind == "vu" and not profile.card_number:
            result.skipped += 1
            result.messages.append(
                f"Pominięto VU bez karty: {profile.vehicle_plate or profile.profile_key}"
            )
            continue
        draft = profile_to_driver_draft(profile)
        if draft is None:
            result.skipped += 1
            continue

        existing = store.find_driver_match(
            profile_key=draft.brudnopis_profile_key,
            card_number=draft.card_number,
        )
        if existing is None:
            store.upsert_driver(draft)
            result.created += 1
            result.messages.append(f"Dodano: {draft.full_name}")
            continue

        changed = False
        if draft.full_name and (
            not existing.full_name
            or existing.full_name == "Kierowca"
            or (draft.full_name != "Kierowca" and existing.full_name != draft.full_name)
        ):
            # Uzupełnij / popraw nazwę z DDD, bez nadpisywania świadomie innej nazwy HR
            # gdy różnica tylko kosmetyczna — aktualizuj gdy brak karty w nazwie źródłowej była słaba
            if existing.full_name.startswith("Kierowca") or existing.full_name == draft.full_name:
                existing.full_name = draft.full_name
                changed = True
            elif not existing.card_number and draft.card_number:
                existing.full_name = draft.full_name
                changed = True
        if draft.card_number and existing.card_number != draft.card_number:
            existing.card_number = draft.card_number
            changed = True
        if (
            draft.brudnopis_profile_key
            and existing.brudnopis_profile_key != draft.brudnopis_profile_key
        ):
            existing.brudnopis_profile_key = draft.brudnopis_profile_key
            changed = True
        if changed:
            store.upsert_driver(existing)
            result.updated += 1
            result.messages.append(f"Zaktualizowano: {existing.full_name}")
        else:
            result.skipped += 1
    return result
