from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from threading import local
from datetime import date, time
from pathlib import Path

from ewidentor.core.comp_ledger import CompEntry, CompKind
from ewidentor.core.milo import MiloStay, sorted_milo_stays
from ewidentor.core.milo_rates import (
    DEFAULT_FX_PLN,
    SEED_COUNTRY_RATES,
    AtSeniority,
    CountryRate,
    OpType,
    at_seniority_code,
    coerce_at_seniority,
    coerce_op_type,
    op_type_code,
)
from ewidentor.core.virtual_diet import DEFAULT_AVG_WAGE_PLN
from ewidentor.core.models import (
    Absence,
    AbsenceKind,
    Attachment,
    Driver,
    DUTY_MODE_HEURISTIC,
    PaySettings,
    _DUTY_MODE_LEGACY_HEURISTIC,
    normalize_duty_mode,
)
from ewidentor.core.paths import attachments_dir, data_dir


def ewidentor_db_path() -> Path:
    return data_dir() / "ewidentor.sqlite"


class EwidentorStore:
    def __init__(self, path: Path | None = None) -> None:
        self._connections = local()
        self.path = path or ewidentor_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._ensure_milo_seed()

    @contextmanager
    def _connect(self):
        active = getattr(self._connections, 'connection', None)
        if active is not None:
            yield active
            return
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """Group store writes atomically; each thread owns its connection."""
        with self._connect() as conn:
            previous = getattr(self._connections, 'connection', None)
            self._connections.connection = conn
            try:
                yield conn
            finally:
                self._connections.connection = previous

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drivers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name TEXT NOT NULL,
                    card_number TEXT,
                    brudnopis_profile_key TEXT,
                    work_system TEXT NOT NULL DEFAULT 'równoważny',
                    employment_fraction REAL NOT NULL DEFAULT 1.0,
                    employer_name TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    monthly_salary REAL NOT NULL DEFAULT 0,
                    hourly_rate REAL,
                    duty_percent REAL NOT NULL DEFAULT 50,
                    night_percent REAL NOT NULL DEFAULT 20,
                    night_start TEXT NOT NULL DEFAULT '22:00',
                    night_end TEXT NOT NULL DEFAULT '06:00',
                    overtime_50_percent REAL NOT NULL DEFAULT 50,
                    overtime_100_percent REAL NOT NULL DEFAULT 100,
                    night_on_availability INTEGER NOT NULL DEFAULT 0,
                    duty_mode TEXT NOT NULL DEFAULT 'raw',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS absences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    day TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    minutes INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    UNIQUE(driver_id, day, kind, title)
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS month_closures (
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    closed_at TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    PRIMARY KEY (driver_id, year, month)
                );
                CREATE TABLE IF NOT EXISTS comp_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    earn_day TEXT NOT NULL,
                    settle_day TEXT,
                    minutes INTEGER NOT NULL DEFAULT 0,
                    rate_percent INTEGER NOT NULL DEFAULT 100,
                    title TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    paid_out INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_comp_driver_earn
                    ON comp_entries(driver_id, earn_day);
                CREATE TABLE IF NOT EXISTS country_rates (
                    country_code TEXT PRIMARY KEY,
                    name_pl TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    hourly_rate REAL NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'seed_2026'
                );
                CREATE TABLE IF NOT EXISTS milo_stays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    day TEXT NOT NULL,
                    country_code TEXT NOT NULL,
                    minutes INTEGER NOT NULL DEFAULT 0,
                    op_type TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_milo_stays_month
                    ON milo_stays(driver_id, day);
                CREATE TABLE IF NOT EXISTS day_overrides (
                    driver_id INTEGER NOT NULL REFERENCES drivers(id) ON DELETE CASCADE,
                    day TEXT NOT NULL,
                    segments_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (driver_id, day)
                );
                """
            )
            self._migrate_driver_columns(conn)

    def _migrate_driver_columns(self, conn: sqlite3.Connection) -> None:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(drivers)").fetchall()}
        if "at_seniority" not in cols:
            conn.execute(
                "ALTER TABLE drivers ADD COLUMN at_seniority TEXT NOT NULL DEFAULT 'a'"
            )
        if "night_on_availability" not in cols:
            conn.execute(
                "ALTER TABLE drivers ADD COLUMN night_on_availability "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "duty_mode" not in cols:
            conn.execute(
                "ALTER TABLE drivers ADD COLUMN duty_mode TEXT NOT NULL DEFAULT 'raw'"
            )
        # Legacy value rename; keep reading old rows via normalize_duty_mode.
        conn.execute(
            "UPDATE drivers SET duty_mode=? WHERE duty_mode=?",
            (DUTY_MODE_HEURISTIC, _DUTY_MODE_LEGACY_HEURISTIC),
        )

    def _ensure_milo_seed(self) -> None:
        with self._connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM country_rates").fetchone()["c"]
            if int(n) == 0:
                for r in SEED_COUNTRY_RATES:
                    conn.execute(
                        """
                        INSERT INTO country_rates (
                            country_code, name_pl, currency, hourly_rate,
                            enabled, note, source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            r.country_code,
                            r.name_pl,
                            r.currency,
                            r.hourly_rate,
                            1 if r.enabled else 0,
                            r.note,
                            r.source,
                        ),
                    )
            for cur, rate in DEFAULT_FX_PLN.items():
                key = f"fx_{cur.lower()}_pln"
                row = conn.execute(
                    "SELECT 1 FROM settings WHERE key=?", (key,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO settings(key, value) VALUES (?, ?)",
                        (key, str(rate)),
                    )
            if (
                conn.execute(
                    "SELECT 1 FROM settings WHERE key=?", ("avg_wage_pln",)
                ).fetchone()
                is None
            ):
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?)",
                    ("avg_wage_pln", str(DEFAULT_AVG_WAGE_PLN)),
                )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def list_drivers(self) -> list[Driver]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM drivers ORDER BY full_name COLLATE NOCASE"
            ).fetchall()
            return [self._driver_from_row(r) for r in rows]

    def get_driver(self, driver_id: int) -> Driver | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM drivers WHERE id = ?", (driver_id,)
            ).fetchone()
            return self._driver_from_row(row) if row else None

    def find_driver_match(
        self,
        *,
        profile_key: str | None = None,
        card_number: str | None = None,
    ) -> Driver | None:
        """Dopasuj istniejącego kierowcę po kluczu Brudnopisu lub numerze karty."""
        with self._connect() as conn:
            if profile_key:
                row = conn.execute(
                    "SELECT * FROM drivers WHERE brudnopis_profile_key = ?",
                    (profile_key,),
                ).fetchone()
                if row:
                    return self._driver_from_row(row)
            card = (card_number or "").strip()
            if card:
                row = conn.execute(
                    "SELECT * FROM drivers WHERE card_number = ?",
                    (card,),
                ).fetchone()
                if row:
                    return self._driver_from_row(row)
        return None

    def upsert_driver(self, driver: Driver) -> Driver:
        pay = driver.pay
        with self._connect() as conn:
            if driver.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO drivers (
                        full_name, card_number, brudnopis_profile_key, work_system,
                        employment_fraction, employer_name, notes,
                        monthly_salary, hourly_rate, duty_percent, night_percent,
                        night_start, night_end, overtime_50_percent, overtime_100_percent,
                        at_seniority, night_on_availability, duty_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        driver.full_name,
                        driver.card_number,
                        driver.brudnopis_profile_key,
                        driver.work_system,
                        driver.employment_fraction,
                        driver.employer_name,
                        driver.notes,
                        float(pay.monthly_salary),
                        float(pay.hourly_rate) if pay.hourly_rate is not None else None,
                        pay.duty_percent,
                        pay.night_percent,
                        pay.night_start.strftime("%H:%M"),
                        pay.night_end.strftime("%H:%M"),
                        pay.overtime_50_percent,
                        pay.overtime_100_percent,
                        at_seniority_code(driver.at_seniority),
                        1 if pay.night_on_availability else 0,
                        normalize_duty_mode(pay.duty_mode),
                    ),
                )
                driver.id = int(cur.lastrowid)
            else:
                cur = conn.execute(
                    """
                    UPDATE drivers SET
                        full_name=?, card_number=?, brudnopis_profile_key=?,
                        work_system=?, employment_fraction=?, employer_name=?, notes=?,
                        monthly_salary=?, hourly_rate=?, duty_percent=?, night_percent=?,
                        night_start=?, night_end=?, overtime_50_percent=?, overtime_100_percent=?,
                        at_seniority=?, night_on_availability=?, duty_mode=?
                    WHERE id=?
                    """,
                    (
                        driver.full_name,
                        driver.card_number,
                        driver.brudnopis_profile_key,
                        driver.work_system,
                        driver.employment_fraction,
                        driver.employer_name,
                        driver.notes,
                        float(pay.monthly_salary),
                        float(pay.hourly_rate) if pay.hourly_rate is not None else None,
                        pay.duty_percent,
                        pay.night_percent,
                        pay.night_start.strftime("%H:%M"),
                        pay.night_end.strftime("%H:%M"),
                        pay.overtime_50_percent,
                        pay.overtime_100_percent,
                        at_seniority_code(driver.at_seniority),
                        1 if pay.night_on_availability else 0,
                        normalize_duty_mode(pay.duty_mode),
                        driver.id,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError(
                        f"Nie zapisano profilu id={driver.id} (rowcount={cur.rowcount})."
                    )
            conn.commit()
        return driver

    def delete_driver(self, driver_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM drivers WHERE id = ?", (driver_id,))

    def _driver_from_row(self, row: sqlite3.Row) -> Driver:
        night_start = _parse_hhmm(row["night_start"], time(22, 0))
        night_end = _parse_hhmm(row["night_end"], time(6, 0))
        keys = row.keys()
        band_raw = row["at_seniority"] if "at_seniority" in keys else "a"
        band = coerce_at_seniority(band_raw)
        night_on_av = False
        if "night_on_availability" in keys:
            night_on_av = bool(int(row["night_on_availability"] or 0))
        duty_mode = "raw"
        if "duty_mode" in keys:
            duty_mode = normalize_duty_mode(str(row["duty_mode"] or "raw"))
        return Driver(
            id=int(row["id"]),
            full_name=str(row["full_name"]),
            card_number=row["card_number"],
            brudnopis_profile_key=row["brudnopis_profile_key"],
            work_system=str(row["work_system"] or "równoważny"),
            employment_fraction=float(row["employment_fraction"] or 1.0),
            employer_name=str(row["employer_name"] or ""),
            notes=str(row["notes"] or ""),
            at_seniority=band,
            pay=PaySettings(
                monthly_salary=float(row["monthly_salary"] or 0),
                hourly_rate=float(row["hourly_rate"]) if row["hourly_rate"] is not None else None,
                duty_percent=float(row["duty_percent"] or 50),
                night_percent=float(row["night_percent"] or 20),
                night_start=night_start,
                night_end=night_end,
                overtime_50_percent=float(row["overtime_50_percent"] or 50),
                overtime_100_percent=float(row["overtime_100_percent"] or 100),
                night_on_availability=night_on_av,
                duty_mode=duty_mode,
            ),
        )

    def list_absences(self, driver_id: int, year: int, month: int) -> list[Absence]:
        prefix = f"{year:04d}-{month:02d}-"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM absences WHERE driver_id = ? AND day LIKE ? ORDER BY day",
                (driver_id, prefix + "%"),
            ).fetchall()
            return [self._absence_from_row(r) for r in rows]

    def upsert_absence(self, absence: Absence) -> Absence:
        with self._connect() as conn:
            if absence.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO absences (driver_id, day, kind, minutes, title, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        absence.driver_id,
                        absence.day.isoformat(),
                        absence.kind.value,
                        absence.minutes,
                        absence.title,
                        absence.note,
                    ),
                )
                absence.id = int(cur.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE absences SET day=?, kind=?, minutes=?, title=?, note=?
                    WHERE id=?
                    """,
                    (
                        absence.day.isoformat(),
                        absence.kind.value,
                        absence.minutes,
                        absence.title,
                        absence.note,
                        absence.id,
                    ),
                )
        return absence

    def delete_absence(self, absence_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM absences WHERE id = ?", (absence_id,))

    def _absence_from_row(self, row: sqlite3.Row) -> Absence:
        return Absence(
            id=int(row["id"]),
            driver_id=int(row["driver_id"]),
            day=date.fromisoformat(row["day"]),
            kind=AbsenceKind(row["kind"]),
            minutes=int(row["minutes"] or 0),
            title=str(row["title"] or ""),
            note=str(row["note"] or ""),
        )

    def list_attachments(self, driver_id: int, year: int, month: int) -> list[Attachment]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM attachments WHERE driver_id=? AND year=? AND month=? ORDER BY id",
                (driver_id, year, month),
            ).fetchall()
            return [
                Attachment(
                    id=int(r["id"]),
                    driver_id=int(r["driver_id"]),
                    year=int(r["year"]),
                    month=int(r["month"]),
                    filename=str(r["filename"]),
                    stored_path=str(r["stored_path"]),
                    note=str(r["note"] or ""),
                )
                for r in rows
            ]

    def add_attachment(
        self,
        driver_id: int,
        year: int,
        month: int,
        source: Path,
        note: str = "",
    ) -> Attachment:
        dest_dir = attachments_dir() / str(driver_id) / f"{year:04d}-{month:02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        if dest.exists():
            stem, suffix = source.stem, source.suffix
            n = 2
            while dest.exists():
                dest = dest_dir / f"{stem}_{n}{suffix}"
                n += 1
        shutil.copy2(source, dest)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO attachments (driver_id, year, month, filename, stored_path, note)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (driver_id, year, month, dest.name, str(dest), note),
            )
            att_id = int(cur.lastrowid)
        return Attachment(
            id=att_id,
            driver_id=driver_id,
            year=year,
            month=month,
            filename=dest.name,
            stored_path=str(dest),
            note=note,
        )

    def delete_attachment(self, attachment_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT stored_path FROM attachments WHERE id=?", (attachment_id,)
            ).fetchone()
            conn.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
        if row:
            path = Path(row["stored_path"])
            if path.is_file():
                path.unlink(missing_ok=True)

    def save_month_closure(
        self, driver_id: int, year: int, month: int, snapshot: dict
    ) -> None:
        from datetime import datetime

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO month_closures (driver_id, year, month, closed_at, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(driver_id, year, month) DO UPDATE SET
                    closed_at=excluded.closed_at,
                    snapshot_json=excluded.snapshot_json
                """,
                (
                    driver_id,
                    year,
                    month,
                    datetime.now().isoformat(timespec="seconds"),
                    json.dumps(snapshot, ensure_ascii=False),
                ),
            )

    def is_month_closed(self, driver_id: int, year: int, month: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM month_closures WHERE driver_id=? AND year=? AND month=?",
                (driver_id, year, month),
            ).fetchone()
            return row is not None

    def list_comp_entries(
        self, driver_id: int, year: int, month: int
    ) -> list[CompEntry]:
        """Wpisy, których earn_day lub settle_day wypada w miesiącu."""
        prefix = f"{year:04d}-{month:02d}-"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM comp_entries
                WHERE driver_id = ?
                  AND (earn_day LIKE ? OR settle_day LIKE ?)
                ORDER BY earn_day, id
                """,
                (driver_id, prefix + "%", prefix + "%"),
            ).fetchall()
            return [self._comp_from_row(r) for r in rows]

    def upsert_comp_entry(self, entry: CompEntry) -> CompEntry:
        with self._connect() as conn:
            if entry.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO comp_entries (
                        driver_id, kind, earn_day, settle_day, minutes,
                        rate_percent, title, note, paid_out
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.driver_id,
                        entry.kind.value,
                        entry.earn_day.isoformat(),
                        entry.settle_day.isoformat() if entry.settle_day else None,
                        entry.minutes,
                        entry.rate_percent,
                        entry.title,
                        entry.note,
                        1 if entry.paid_out else 0,
                    ),
                )
                entry.id = int(cur.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE comp_entries SET
                        kind=?, earn_day=?, settle_day=?, minutes=?,
                        rate_percent=?, title=?, note=?, paid_out=?
                    WHERE id=?
                    """,
                    (
                        entry.kind.value,
                        entry.earn_day.isoformat(),
                        entry.settle_day.isoformat() if entry.settle_day else None,
                        entry.minutes,
                        entry.rate_percent,
                        entry.title,
                        entry.note,
                        1 if entry.paid_out else 0,
                        entry.id,
                    ),
                )
        return entry

    def delete_comp_entry(self, entry_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM comp_entries WHERE id=?", (entry_id,))

    def _comp_from_row(self, row: sqlite3.Row) -> CompEntry:
        return CompEntry(
            id=int(row["id"]),
            driver_id=int(row["driver_id"]),
            kind=CompKind(row["kind"]),
            earn_day=date.fromisoformat(row["earn_day"]),
            settle_day=date.fromisoformat(row["settle_day"]) if row["settle_day"] else None,
            minutes=int(row["minutes"] or 0),
            rate_percent=int(row["rate_percent"] or 100),
            title=str(row["title"] or ""),
            note=str(row["note"] or ""),
            paid_out=bool(row["paid_out"]),
        )

    def list_country_rates(self) -> list[CountryRate]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM country_rates ORDER BY country_code"
            ).fetchall()
            return [
                CountryRate(
                    country_code=str(r["country_code"]),
                    name_pl=str(r["name_pl"]),
                    currency=str(r["currency"]),
                    hourly_rate=float(r["hourly_rate"]),
                    enabled=bool(r["enabled"]),
                    note=str(r["note"] or ""),
                    source=str(r["source"] or ""),
                )
                for r in rows
            ]

    def upsert_country_rate(self, rate: CountryRate) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO country_rates (
                    country_code, name_pl, currency, hourly_rate, enabled, note, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(country_code) DO UPDATE SET
                    name_pl=excluded.name_pl,
                    currency=excluded.currency,
                    hourly_rate=excluded.hourly_rate,
                    enabled=excluded.enabled,
                    note=excluded.note,
                    source=excluded.source
                """,
                (
                    rate.country_code.upper(),
                    rate.name_pl,
                    rate.currency.upper(),
                    rate.hourly_rate,
                    1 if rate.enabled else 0,
                    rate.note,
                    rate.source,
                ),
            )

    def get_fx_pln(
        self, *, year: int | None = None, month: int | None = None
    ) -> dict[str, float]:
        out = dict(DEFAULT_FX_PLN)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key LIKE 'fx_%_pln'"
            ).fetchall()
            for r in rows:
                key = str(r["key"])
                # fx_eur_pln → EUR; pomiń klucze miesięczne fx_eur_pln_YYYY_MM
                body = key[3:-4]  # strip fx_ … _pln
                if "_" in body and body.split("_")[-1].isdigit():
                    continue
                cur = body.upper()
                try:
                    out[cur] = float(r["value"])
                except ValueError:
                    continue
        if year is not None and month is not None:
            raw = self.get_setting(f"fx_eur_pln_{year:04d}_{month:02d}", "")
            if raw:
                try:
                    out["EUR"] = float(raw)
                except ValueError:
                    pass
        return out

    def get_month_fx_eur(self, year: int, month: int) -> float | None:
        raw = self.get_setting(f"fx_eur_pln_{year:04d}_{month:02d}", "")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def set_month_fx_eur(self, year: int, month: int, rate: float | None) -> None:
        key = f"fx_eur_pln_{year:04d}_{month:02d}"
        if rate is None or rate <= 0:
            with self._connect() as conn:
                conn.execute("DELETE FROM settings WHERE key=?", (key,))
            return
        self.set_setting(key, str(rate))

    def set_fx_pln(self, currency: str, rate: float) -> None:
        self.set_setting(f"fx_{currency.lower()}_pln", str(rate))

    def get_avg_wage_pln(self) -> float:
        raw = self.get_setting("avg_wage_pln", str(DEFAULT_AVG_WAGE_PLN))
        try:
            return float(raw)
        except ValueError:
            return DEFAULT_AVG_WAGE_PLN

    def set_avg_wage_pln(self, value: float) -> None:
        self.set_setting("avg_wage_pln", str(value))

    def list_milo_stays(self, driver_id: int, year: int, month: int) -> list[MiloStay]:
        prefix = f"{year:04d}-{month:02d}-"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM milo_stays
                WHERE driver_id=? AND day LIKE ?
                ORDER BY day, id
                """,
                (driver_id, prefix + "%"),
            ).fetchall()
            return sorted_milo_stays([self._milo_from_row(r) for r in rows])

    def upsert_milo_stay(self, stay: MiloStay) -> MiloStay:
        with self._connect() as conn:
            if stay.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO milo_stays (
                        driver_id, day, country_code, minutes, op_type, note
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stay.driver_id,
                        stay.day.isoformat(),
                        stay.country_code.upper(),
                        stay.minutes,
                        op_type_code(stay.op_type),
                        stay.note,
                    ),
                )
                stay.id = int(cur.lastrowid)
            else:
                conn.execute(
                    """
                    UPDATE milo_stays SET
                        day=?, country_code=?, minutes=?, op_type=?, note=?
                    WHERE id=?
                    """,
                    (
                        stay.day.isoformat(),
                        stay.country_code.upper(),
                        stay.minutes,
                        op_type_code(stay.op_type),
                        stay.note,
                        stay.id,
                    ),
                )
        return stay

    def delete_milo_stay(self, stay_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM milo_stays WHERE id=?", (stay_id,))

    def get_day_override(
        self, driver_id: int, day: date
    ) -> list[dict] | None:
        """None = brak nadpisania; [] = dzień ręcznie wyczyszczony."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT segments_json FROM day_overrides WHERE driver_id=? AND day=?",
                (driver_id, day.isoformat()),
            ).fetchone()
        if row is None:
            return None
        raw = json.loads(row["segments_json"] or "[]")
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def set_day_override(
        self, driver_id: int, day: date, segments: list[dict]
    ) -> None:
        payload = json.dumps(segments, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO day_overrides (driver_id, day, segments_json, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(driver_id, day) DO UPDATE SET
                    segments_json=excluded.segments_json,
                    updated_at=datetime('now')
                """,
                (driver_id, day.isoformat(), payload),
            )
            conn.commit()

    def clear_day_override(self, driver_id: int, day: date) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM day_overrides WHERE driver_id=? AND day=?",
                (driver_id, day.isoformat()),
            )
            conn.commit()

    def list_day_overrides(
        self, driver_id: int, year: int, month: int
    ) -> dict[date, list[dict]]:
        prefix = f"{year:04d}-{month:02d}-"
        out: dict[date, list[dict]] = {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT day, segments_json FROM day_overrides
                WHERE driver_id=? AND day LIKE ?
                ORDER BY day
                """,
                (driver_id, f"{prefix}%"),
            ).fetchall()
        for row in rows:
            day = date.fromisoformat(row["day"])
            raw = json.loads(row["segments_json"] or "[]")
            out[day] = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        return out

    def _milo_from_row(self, row: sqlite3.Row) -> MiloStay:
        return MiloStay(
            id=int(row["id"]),
            driver_id=int(row["driver_id"]),
            day=date.fromisoformat(row["day"]),
            country_code=str(row["country_code"]),
            minutes=int(row["minutes"] or 0),
            op_type=coerce_op_type(row["op_type"]),
            note=str(row["note"] or ""),
        )


def _parse_hhmm(value: str | None, default: time) -> time:
    if not value:
        return default
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except Exception:
        return default
