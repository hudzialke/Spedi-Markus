"""Zlecenia Dispatchera jako podpowiedź typu MiLo (nie silnik płac)."""

from __future__ import annotations

import calendar
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ewidentor.core.milo_rates import OpType

HOME_COUNTRY = "PL"

KNOWN_CC = frozenset(
    {
        "PL",
        "DE",
        "FR",
        "ES",
        "IT",
        "NL",
        "BE",
        "AT",
        "CZ",
        "SK",
        "HU",
        "LT",
        "LV",
        "EE",
        "RO",
        "BG",
        "HR",
        "SI",
        "PT",
        "DK",
        "SE",
        "FI",
        "IE",
        "LU",
        "CH",
        "GB",
        "UK",
        "NO",
    }
)

# ( DE ) / (NL)
_PAREN_CC_RE = re.compile(r"\(\s*([A-Za-z]{2})\s*\)")
# SK 060 01 / PL 83-010 / IT-21010
_POSTAL_CC_RE = re.compile(
    r"\b([A-Za-z]{2})[-\s]+(?:\d{3}\s\d{2}|\d{2}-\d{3}|\d{4,5})\b"
)
# 1042 Amsterdam, NL
_TRAIL_CC_RE = re.compile(r",\s*([A-Za-z]{2})\s*$", re.MULTILINE)
# 19 rozł w AT
_PL_IN_CC_RE = re.compile(r"\bw\s+([A-Za-z]{2})\b", re.IGNORECASE)

_COUNTRY_NAMES: tuple[tuple[str, str], ...] = (
    ("slovakia", "SK"),
    ("slowacja", "SK"),
    ("słowacja", "SK"),
    ("germany", "DE"),
    ("deutschland", "DE"),
    ("niemcy", "DE"),
    ("netherlands", "NL"),
    ("niderlandy", "NL"),
    ("holandia", "NL"),
    ("austria", "AT"),
    ("czechia", "CZ"),
    ("czech republic", "CZ"),
    ("czechy", "CZ"),
    ("italy", "IT"),
    ("italia", "IT"),
    ("włochy", "IT"),
    ("wlochy", "IT"),
    ("france", "FR"),
    ("francja", "FR"),
    ("belgium", "BE"),
    ("belgia", "BE"),
    ("poland", "PL"),
    ("polska", "PL"),
    ("hungary", "HU"),
    ("węgry", "HU"),
    ("wegry", "HU"),
    ("spain", "ES"),
    ("hiszpania", "ES"),
)

_FOLD = str.maketrans(
    "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ",
    "acelnoszzACELNOSZZ",
)

_DIM_RE = re.compile(r"\d+x\d+", re.IGNORECASE)
_NON_LETTER_RE = re.compile(r"[^a-z]+")


@dataclass(frozen=True, slots=True)
class DispatcherLeg:
    load_day: date | None
    unload_day: date | None
    load_cc: str
    unload_ccs: tuple[str, ...]
    driver_name: str = ""
    job_id: int | None = None


@dataclass(frozen=True, slots=True)
class DispatcherOp:
    op: OpType
    reason: str


def default_dispatcher_db() -> Path:
    try:
        from markus_dispatcher.config import DB_PATH

        return Path(DB_PATH)
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "Spedi" / "MarkusDispatcher" / "markus_dispatcher.db"
    try:
        from platformdirs import user_data_dir

        return Path(user_data_dir("MarkusDispatcher", "Spedi")) / "markus_dispatcher.db"
    except Exception:
        return (
            Path.home()
            / "AppData"
            / "Local"
            / "Spedi"
            / "MarkusDispatcher"
            / "markus_dispatcher.db"
        )


def extract_country_codes(address: str) -> tuple[str, ...]:
    """Kody ISO z adresu zlecenia (nawias, kod pocztowy, nazwa kraju)."""
    text = address or ""
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        cc = (raw or "").strip().upper()
        if cc not in KNOWN_CC or cc in seen:
            return
        seen.add(cc)
        found.append(cc)

    for rx in (_PAREN_CC_RE, _POSTAL_CC_RE, _TRAIL_CC_RE, _PL_IN_CC_RE):
        for match in rx.finditer(text):
            add(match.group(1))

    folded = _fold(text)
    for name, cc in _COUNTRY_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", folded):
            add(cc)

    return tuple(found)


def parse_job_date(text: str) -> date | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for fmt in (
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _fold(text: str) -> str:
    return (text or "").translate(_FOLD).casefold()


def _name_tokens(text: str) -> set[str]:
    cleaned = _DIM_RE.sub(" ", _fold(text))
    return {t for t in _NON_LETTER_RE.split(cleaned) if len(t) >= 3}


def drivers_match(ewidentor_name: str, dispatcher_name: str) -> bool:
    """Match a driver's name with a name followed by vehicle dimensions."""
    a = _name_tokens(ewidentor_name)
    b = _name_tokens(dispatcher_name)
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return shorter <= longer


def matching_leg(
    day: date, country_code: str, legs: list[DispatcherLeg] | tuple[DispatcherLeg, ...]
) -> DispatcherLeg | None:
    cc = (country_code or "").upper()
    if not cc or cc == HOME_COUNTRY:
        return None
    hits = [leg for leg in legs if _leg_covers(leg, day, cc)]
    if not hits:
        return None
    hits.sort(key=lambda leg: (0 if (leg.load_cc or "").upper() == cc else 1, leg.job_id or 0))
    return hits[0]


def classify_stay_from_dispatcher(
    day: date,
    country_code: str,
    legs: list[DispatcherLeg] | tuple[DispatcherLeg, ...] | None,
) -> DispatcherOp | None:
    if not legs:
        return None
    cc = (country_code or "").upper()
    if not cc or cc == HOME_COUNTRY:
        return None
    leg = matching_leg(day, cc, legs)
    if leg is None:
        return None
    return _op_from_leg(cc, leg)


def load_dispatcher_legs_for_driver(
    driver_name: str,
    year: int,
    month: int,
    *,
    db_path: Path | None = None,
) -> list[DispatcherLeg]:
    path = Path(db_path) if db_path is not None else default_dispatcher_db()
    if not driver_name or not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        try:
            drivers = conn.execute(
                "SELECT id, display_name FROM drivers"
            ).fetchall()
        except sqlite3.Error:
            return []
        matched_ids = [
            int(row["id"])
            for row in drivers
            if drivers_match(driver_name, row["display_name"] or "")
        ]
        if not matched_ids:
            return []
        placeholders = ",".join("?" * len(matched_ids))
        try:
            jobs = conn.execute(
                f"""
                SELECT id, driver_id, load_address, unload_address,
                       load_datetime, unload_datetime
                FROM jobs
                WHERE driver_id IN ({placeholders})
                  AND IFNULL(status, '') != 'cancelled'
                """,
                matched_ids,
            ).fetchall()
        except sqlite3.Error:
            return []
        names = {
            int(row["id"]): (row["display_name"] or "")
            for row in drivers
            if int(row["id"]) in matched_ids
        }
    finally:
        conn.close()

    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    out: list[DispatcherLeg] = []
    for row in jobs:
        load_day = parse_job_date(row["load_datetime"] or "")
        unload_day = parse_job_date(row["unload_datetime"] or "")
        if not _overlaps_month(load_day, unload_day, month_start, month_end):
            continue
        load_codes = extract_country_codes(row["load_address"] or "")
        unload_codes = extract_country_codes(row["unload_address"] or "")
        load_cc = load_codes[0] if load_codes else ""
        if not load_cc and not unload_codes:
            continue
        driver_id = row["driver_id"]
        out.append(
            DispatcherLeg(
                load_day=load_day,
                unload_day=unload_day,
                load_cc=load_cc,
                unload_ccs=unload_codes,
                driver_name=names.get(int(driver_id), "") if driver_id is not None else "",
                job_id=int(row["id"]) if row["id"] is not None else None,
            )
        )
    return out


def _leg_covers(leg: DispatcherLeg, day: date, country_code: str) -> bool:
    involved = { (leg.load_cc or "").upper() } | {c.upper() for c in leg.unload_ccs}
    involved.discard("")
    if country_code not in involved:
        return False
    start = leg.load_day or leg.unload_day
    end = leg.unload_day or leg.load_day
    if start is None or end is None:
        return False
    if start > end:
        start, end = end, start
    return start <= day <= end


def _overlaps_month(
    load_day: date | None,
    unload_day: date | None,
    month_start: date,
    month_end: date,
) -> bool:
    start = load_day or unload_day
    end = unload_day or load_day
    if start is None or end is None:
        return False
    if start > end:
        start, end = end, start
    return start <= month_end and end >= month_start


def _op_from_leg(stay_cc: str, leg: DispatcherLeg) -> DispatcherOp:
    load = (leg.load_cc or "").upper()
    unloads = tuple(c.upper() for c in leg.unload_ccs if c)
    unload_set = set(unloads)
    foreign_unloads = {c for c in unloads if c != HOME_COUNTRY}
    dest = "/".join(dict.fromkeys(c for c in unloads if c and c != load)) or (
        "/".join(unloads) if unloads else "?"
    )

    if (
        load == stay_cc
        and stay_cc in unload_set
        and stay_cc != HOME_COUNTRY
        and unload_set <= {stay_cc}
    ):
        return DispatcherOp(
            OpType.CABOTAGE,
            f"Załadunek i rozładunek w {stay_cc} — kabotaż",
        )

    if load == stay_cc and load != HOME_COUNTRY:
        if foreign_unloads - {load}:
            return DispatcherOp(
                OpType.CROSS_TRADE,
                f"Załadunek {load}, rozładunek {dest} — cross-trade, nie tranzyt",
            )
        if HOME_COUNTRY in unload_set and not (foreign_unloads - {load}):
            return DispatcherOp(
                OpType.BILATERAL,
                f"Załadunek {load}, rozładunek PL — bilateral",
            )
        return DispatcherOp(
            OpType.CROSS_TRADE,
            f"Załadunek w {load} (Dispatcher) — nie tranzyt",
        )

    if stay_cc in unload_set:
        if load == HOME_COUNTRY or not load:
            return DispatcherOp(
                OpType.BILATERAL,
                f"Rozładunek {stay_cc}, załadunek {load or 'PL'} — bilateral",
            )
        return DispatcherOp(
            OpType.CROSS_TRADE,
            f"Załadunek {load}, rozładunek {stay_cc} — cross-trade",
        )

    return DispatcherOp(
        OpType.CROSS_TRADE,
        f"Zlecenie Dispatcher: zał. {load or '?'}, rozł. {dest}",
    )
