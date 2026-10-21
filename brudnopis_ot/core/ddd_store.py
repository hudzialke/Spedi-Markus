from __future__ import annotations

import json
from contextlib import contextmanager
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from brudnopis_ot.core.ddd_file_loader import (
    BorderCrossingRecord,
    DddLoadResult,
    GnssPointRecord,
    PlaceRecord,
    SourceKind,
    _normalize_plate,
    merge_load_into_profile,
)
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.paths import data_dir
from brudnopis_ot.core.models import Scenario


def ddd_db_path() -> Path:
    return data_dir() / "ddd_profiles.sqlite"


class DddProfileStore:
    """Trwała baza profili DDD (karta / VU) przy katalogu aplikacji."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or ddd_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        active = getattr(self, "_active_connection", None)
        if active is not None:
            yield active
            return
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self._active_connection = conn
        try:
            with conn:
                yield conn
        finally:
            self._active_connection = None
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_key TEXT PRIMARY KEY,
                    source_kind TEXT NOT NULL,
                    card_number TEXT,
                    vehicle_plate TEXT,
                    driver_name TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    filenames_json TEXT NOT NULL DEFAULT '[]',
                    date_from TEXT,
                    date_to TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    day TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    start_minute INTEGER NOT NULL,
                    end_minute INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_segments_profile_day
                    ON segments(profile_key, day);
                CREATE TABLE IF NOT EXISTS imported_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    date_from TEXT,
                    date_to TEXT,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS border_crossings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    occurred_at TEXT NOT NULL,
                    country_left TEXT NOT NULL,
                    country_entered TEXT NOT NULL,
                    odometer_km INTEGER,
                    source TEXT NOT NULL DEFAULT 'card',
                    UNIQUE(profile_key, occurred_at, country_left, country_entered)
                );
                CREATE INDEX IF NOT EXISTS idx_border_crossings_profile
                    ON border_crossings(profile_key, occurred_at);
                CREATE TABLE IF NOT EXISTS places (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    occurred_at TEXT NOT NULL,
                    entry_type TEXT NOT NULL,
                    country_code TEXT NOT NULL,
                    odometer_km INTEGER,
                    UNIQUE(profile_key, occurred_at, entry_type, country_code)
                );
                CREATE INDEX IF NOT EXISTS idx_places_profile
                    ON places(profile_key, occurred_at);
                CREATE TABLE IF NOT EXISTS gnss_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    occurred_at TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    odometer_km INTEGER,
                    UNIQUE(profile_key, occurred_at, latitude, longitude)
                );
                CREATE INDEX IF NOT EXISTS idx_gnss_points_profile
                    ON gnss_points(profile_key, occurred_at);
                """
            )
            self._ensure_column(conn, "border_crossings", "source", "TEXT NOT NULL DEFAULT 'card'")
            self._ensure_column(conn, "profiles", "cycle_start", "TEXT")
            self._ensure_column(conn, "profiles", "scenario_json", "TEXT")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS archived_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    stored_path TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    UNIQUE(profile_key, stored_path)
                );
                CREATE INDEX IF NOT EXISTS idx_archived_files_profile
                    ON archived_files(profile_key);
                CREATE TABLE IF NOT EXISTS ddd_scenario_edits (
                    profile_key TEXT PRIMARY KEY REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    baseline_json TEXT NOT NULL,
                    scenario_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ddd_data_issues (
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    start TEXT NOT NULL,
                    end TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    UNIQUE(profile_key, start, end, reason)
                );
                """
            )

    def load_ddd_scenario(self, profile_key: str, *, original: bool = False) -> Scenario:
        from brudnopis_ot.core.profile_scenario import original_scenario, exact_scenario, rebase_scenario
        profile = self.get(profile_key)
        if profile is None or profile.source_kind == 'template':
            raise ValueError('Wybierz profil DDD.')
        source = original_scenario(profile)
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM ddd_scenario_edits WHERE profile_key=?', (profile_key,)).fetchone()
        if not original and row:
            return rebase_scenario(exact_scenario(json.loads(row['baseline_json'])),
                                   exact_scenario(json.loads(row['scenario_json'])), source)
        cycle = self.get_cycle_start(profile_key)
        if cycle:
            source.week_start = cycle
        return source

    def save_ddd_scenario(self, profile_key: str, scenario: Scenario) -> None:
        from brudnopis_ot.core.profile_scenario import original_scenario
        from brudnopis_ot.core.serialize import scenario_to_dict
        profile = self.get(profile_key)
        if profile is None or profile.source_kind == 'template':
            raise ValueError('Wybierz profil DDD.')
        with self._connect() as conn:
            conn.execute('INSERT OR REPLACE INTO ddd_scenario_edits VALUES (?, ?, ?)',
                         (profile_key, json.dumps(scenario_to_dict(original_scenario(profile)), ensure_ascii=False),
                          json.dumps(scenario_to_dict(scenario), ensure_ascii=False)))
            self.set_cycle_start(profile_key, scenario.week_start)

    def add_data_issue(self, profile_key: str, start: datetime, end: datetime, reason: str) -> None:
        with self._connect() as conn:
            conn.execute('INSERT OR IGNORE INTO ddd_data_issues VALUES (?, ?, ?, ?)',
                         (profile_key, start.isoformat(), end.isoformat(), reason))

    def get_data_issues(self, profile_key: str) -> list[tuple[datetime, datetime, str]]:
        with self._connect() as conn:
            rows = conn.execute('SELECT start, end, reason FROM ddd_data_issues WHERE profile_key=? ORDER BY start',
                                (profile_key,)).fetchall()
        return [(datetime.fromisoformat(r['start']), datetime.fromisoformat(r['end']), r['reason']) for r in rows]

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, decl: str
    ) -> None:
        cols = {
            str(r[1])
            for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def list_profiles(self) -> list[DddLoadResult]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT profile_key FROM profiles ORDER BY source_kind, driver_name, vehicle_plate"
            ).fetchall()
            return [self._load_row(conn, r["profile_key"]) for r in rows]

    def get(self, profile_key: str) -> DddLoadResult | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT profile_key FROM profiles WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
            if row is None:
                return None
            return self._load_row(conn, profile_key)

    def _load_row(self, conn: sqlite3.Connection, profile_key: str) -> DddLoadResult:
        meta = conn.execute(
            "SELECT * FROM profiles WHERE profile_key = ?", (profile_key,)
        ).fetchone()
        segs = conn.execute(
            "SELECT day, activity, start_minute, end_minute FROM segments "
            "WHERE profile_key = ? ORDER BY day, start_minute, end_minute",
            (profile_key,),
        ).fetchall()
        segments = [
            RawSegment(
                day=date.fromisoformat(s["day"]),
                activity=str(s["activity"]),
                start_minute=int(s["start_minute"]),
                end_minute=int(s["end_minute"]),
            )
            for s in segs
        ]
        crossings: list[BorderCrossingRecord] = []
        try:
            bc_rows = conn.execute(
                "SELECT occurred_at, country_left, country_entered, odometer_km, "
                "COALESCE(source, 'card') AS source "
                "FROM border_crossings WHERE profile_key = ? ORDER BY occurred_at",
                (profile_key,),
            ).fetchall()
            for r in bc_rows:
                crossings.append(
                    BorderCrossingRecord(
                        occurred_at=datetime.fromisoformat(r["occurred_at"]),
                        country_left=str(r["country_left"]),
                        country_entered=str(r["country_entered"]),
                        odometer_km=int(r["odometer_km"])
                        if r["odometer_km"] is not None
                        else None,
                        source=str(r["source"] or "card"),
                    )
                )
        except sqlite3.OperationalError:
            # Stara baza bez tabeli / kolumny — migracja przy następnym _init_schema
            crossings = []
        places: list[PlaceRecord] = []
        try:
            pl_rows = conn.execute(
                "SELECT occurred_at, entry_type, country_code, odometer_km "
                "FROM places WHERE profile_key = ? ORDER BY occurred_at",
                (profile_key,),
            ).fetchall()
            for r in pl_rows:
                places.append(
                    PlaceRecord(
                        occurred_at=datetime.fromisoformat(r["occurred_at"]),
                        entry_type=str(r["entry_type"] or ""),
                        country_code=str(r["country_code"] or ""),
                        odometer_km=int(r["odometer_km"])
                        if r["odometer_km"] is not None
                        else None,
                    )
                )
        except sqlite3.OperationalError:
            places = []
        gnss_points: list[GnssPointRecord] = []
        try:
            gp_rows = conn.execute(
                "SELECT occurred_at, latitude, longitude, odometer_km "
                "FROM gnss_points WHERE profile_key = ? ORDER BY occurred_at",
                (profile_key,),
            ).fetchall()
            for r in gp_rows:
                gnss_points.append(
                    GnssPointRecord(
                        occurred_at=datetime.fromisoformat(r["occurred_at"]),
                        latitude=float(r["latitude"]),
                        longitude=float(r["longitude"]),
                        odometer_km=int(r["odometer_km"])
                        if r["odometer_km"] is not None
                        else None,
                    )
                )
        except sqlite3.OperationalError:
            gnss_points = []
        filenames = tuple(json.loads(meta["filenames_json"] or "[]"))
        return DddLoadResult(
            template_id=profile_key.removeprefix("template:") if meta["source_kind"] == "template" else None,
            segments=segments,
            title=str(meta["title"] or ""),
            driver_name=str(meta["driver_name"] or ""),
            card_number=meta["card_number"],
            vehicle_plate=meta["vehicle_plate"],
            source_kind=meta["source_kind"],  # type: ignore[arg-type]
            filenames=filenames,
            date_from=date.fromisoformat(meta["date_from"]) if meta["date_from"] else None,
            date_to=date.fromisoformat(meta["date_to"]) if meta["date_to"] else None,
            border_crossings=tuple(crossings),
            places=tuple(places),
            gnss_points=tuple(gnss_points),
        )

    def upsert(self, profile: DddLoadResult) -> DddLoadResult:
        """Zapisz / nadpisz cały profil (segmenty = stan po merge)."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO profiles (
                    profile_key, source_kind, card_number, vehicle_plate, driver_name,
                    title, filenames_json, date_from, date_to, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_key) DO UPDATE SET
                    source_kind=excluded.source_kind,
                    card_number=excluded.card_number,
                    vehicle_plate=excluded.vehicle_plate,
                    driver_name=excluded.driver_name,
                    title=excluded.title,
                    filenames_json=excluded.filenames_json,
                    date_from=excluded.date_from,
                    date_to=excluded.date_to,
                    updated_at=excluded.updated_at
                """,
                (
                    profile.profile_key,
                    profile.source_kind,
                    profile.card_number,
                    profile.vehicle_plate,
                    profile.driver_name,
                    profile.title,
                    json.dumps(list(profile.filenames), ensure_ascii=False),
                    profile.date_from.isoformat() if profile.date_from else None,
                    profile.date_to.isoformat() if profile.date_to else None,
                    now,
                ),
            )
            conn.execute("DELETE FROM segments WHERE profile_key = ?", (profile.profile_key,))
            conn.executemany(
                "INSERT INTO segments (profile_key, day, activity, start_minute, end_minute) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        profile.profile_key,
                        s.day.isoformat(),
                        s.activity,
                        s.start_minute,
                        s.end_minute,
                    )
                    for s in profile.segments
                ],
            )
            conn.execute(
                "DELETE FROM border_crossings WHERE profile_key = ?",
                (profile.profile_key,),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO border_crossings "
                "(profile_key, occurred_at, country_left, country_entered, odometer_km, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        profile.profile_key,
                        c.occurred_at.isoformat(timespec="seconds"),
                        c.country_left,
                        c.country_entered,
                        c.odometer_km,
                        c.source or "card",
                    )
                    for c in profile.border_crossings
                ],
            )
            conn.execute(
                "DELETE FROM places WHERE profile_key = ?",
                (profile.profile_key,),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO places "
                "(profile_key, occurred_at, entry_type, country_code, odometer_km) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        profile.profile_key,
                        p.occurred_at.isoformat(timespec="seconds"),
                        p.entry_type,
                        p.country_code,
                        p.odometer_km,
                    )
                    for p in profile.places
                ],
            )
            conn.execute(
                "DELETE FROM gnss_points WHERE profile_key = ?",
                (profile.profile_key,),
            )
            conn.executemany(
                "INSERT OR IGNORE INTO gnss_points "
                "(profile_key, occurred_at, latitude, longitude, odometer_km) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        profile.profile_key,
                        g.occurred_at.isoformat(timespec="seconds"),
                        g.latitude,
                        g.longitude,
                        g.odometer_km,
                    )
                    for g in profile.gnss_points
                ],
            )
            for name in profile.filenames:
                if conn.execute("SELECT 1 FROM imported_files WHERE profile_key=? AND filename=?",
                                (profile.profile_key, name)).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO imported_files (profile_key, filename, date_from, date_to, imported_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        profile.profile_key,
                        name,
                        profile.date_from.isoformat() if profile.date_from else None,
                        profile.date_to.isoformat() if profile.date_to else None,
                        now,
                    ),
                )
        return profile

    def merge_incoming(self, incoming: DddLoadResult) -> DddLoadResult:
        """Scal z istniejącym profilem o tym samym kluczu (chronologiczny replace_range)."""
        existing = self.get(incoming.profile_key)
        merged = merge_load_into_profile(existing, incoming)
        return self.upsert(merged)

    def find_matches(self, incoming: DddLoadResult) -> list[DddLoadResult]:
        """
        Znajdź istniejące profile pasujące do importu.
        Kolejność: dokładny profile_key → karta → rejestracja → nazwisko.
        """
        exact = self.get(incoming.profile_key)
        if exact is not None:
            return [exact]

        with self._connect() as conn:
            candidates: list[str] = []
            card = (incoming.card_number or "").strip()
            if card and card.upper() not in {"", "N/A"}:
                rows = conn.execute(
                    "SELECT profile_key FROM profiles WHERE card_number = ?",
                    (card,),
                ).fetchall()
                candidates.extend(r["profile_key"] for r in rows)

            plate = _normalize_plate(incoming.vehicle_plate)
            if plate:
                rows = conn.execute(
                    "SELECT profile_key FROM profiles WHERE REPLACE(UPPER(COALESCE(vehicle_plate,'')), ' ', '') = ?",
                    (plate,),
                ).fetchall()
                candidates.extend(r["profile_key"] for r in rows)
                # VU key shape
                vu_key = f"vu:{plate}"
                if self.get(vu_key) and vu_key not in candidates:
                    candidates.append(vu_key)

            name = (incoming.driver_name or "").strip().casefold()
            if name:
                rows = conn.execute(
                    "SELECT profile_key FROM profiles WHERE lower(driver_name) = ?",
                    (name,),
                ).fetchall()
                candidates.extend(r["profile_key"] for r in rows)

        # Unikaj duplikatów, zachowaj kolejność
        seen: set[str] = set()
        out: list[DddLoadResult] = []
        for key in candidates:
            if key in seen:
                continue
            seen.add(key)
            loaded = self.get(key)
            if loaded is not None and loaded.source_kind != "template":
                out.append(loaded)
        return out

    def resolve_and_merge(
        self, incoming: DddLoadResult
    ) -> tuple[DddLoadResult, str]:
        """
        Dopasuj i scal. Zwraca (profil, status):
        'exact' | 'matched' | 'created' | 'conflict'
        Przy konflikcie (>1 różne profile) nie zapisuje — zwraca incoming i 'conflict'.
        """
        existing = self.get(incoming.profile_key)
        if incoming.source_kind == "template":
            raise ValueError("Import DDD nie może zmieniać Template Driver")
        return self.merge_incoming(incoming), "exact" if existing else "created"

    def create_template(self, name: str, cycle_start: date) -> DddLoadResult:
        from uuid import uuid4
        from brudnopis_ot.core.calendar_weeks import ensure_monday
        from brudnopis_ot.core.models import Scenario
        from brudnopis_ot.core.serialize import scenario_to_dict

        name = name.strip()
        if not name:
            raise ValueError("Podaj nazwę ręcznego profilu")
        profile = DddLoadResult(
            segments=[], title=name, driver_name=name, card_number=None,
            vehicle_plate=None, source_kind="template", filenames=(),
            date_from=None, date_to=None, template_id=uuid4().hex,
        )
        scenario = Scenario(week_start=ensure_monday(cycle_start), title=name)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO profiles (profile_key, source_kind, driver_name, title, "
                "updated_at, cycle_start, scenario_json) VALUES (?, 'template', ?, ?, ?, ?, ?)",
                (profile.profile_key, name, name, datetime.now().isoformat(),
                 scenario.week_start.isoformat(), json.dumps(scenario_to_dict(scenario), ensure_ascii=False)),
            )
        return profile

    def load_template_scenario(self, profile_key: str) -> Scenario:
        from brudnopis_ot.core.models import Scenario
        from brudnopis_ot.core.serialize import block_from_dict

        with self._connect() as conn:
            row = conn.execute(
                "SELECT scenario_json FROM profiles WHERE profile_key = ? AND source_kind = 'template'",
                (profile_key,),
            ).fetchone()
        if row is None or not row["scenario_json"]:
            raise ValueError("Nie znaleziono scenariusza ręcznego profilu")
        data = json.loads(row["scenario_json"])
        # Keep individual editable blocks and anchor IDs; JSON file import coalesces blocks.
        return Scenario(
            week_start=date.fromisoformat(data["week_start"]), weeks=data["weeks"],
            employer_country=data["employer_country"], title=data["title"],
            blocks=[block_from_dict(b) for b in data["blocks"]],
            anchor_block_id=data.get("anchor_block_id"),
        )

    def save_template_scenario(self, profile_key: str, scenario: Scenario) -> None:
        from brudnopis_ot.core.serialize import scenario_to_dict

        name = scenario.title.strip()
        if not name:
            raise ValueError("Podaj nazwę ręcznego profilu")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE profiles SET scenario_json = ?, cycle_start = ?, title = ?, "
                "driver_name = ?, updated_at = ? WHERE profile_key = ? AND source_kind = 'template'",
                (json.dumps(scenario_to_dict(scenario), ensure_ascii=False),
                 scenario.week_start.isoformat(), name, name, datetime.now().isoformat(), profile_key),
            )
            if cursor.rowcount != 1:
                raise ValueError("Nie znaleziono ręcznego profilu")

    def delete_template(self, profile_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profiles WHERE profile_key = ? AND source_kind = 'template'", (profile_key,))

    def get_cycle_start(self, profile_key: str) -> date | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cycle_start FROM profiles WHERE profile_key = ?",
                (profile_key,),
            ).fetchone()
        if row is None or not row["cycle_start"]:
            return None
        return date.fromisoformat(str(row["cycle_start"])[:10])

    def set_cycle_start(self, profile_key: str, value: date | None) -> None:
        iso = value.isoformat() if value else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE profiles SET cycle_start = ? WHERE profile_key = ?",
                (iso, profile_key),
            )

    def archive_source_files(
        self, profile_key: str, paths: list[str | Path]
    ) -> list[Path]:
        """Skopiuj .ddd do dane/ddd_files/<profil>/ — Analyzer może je potem otworzyć."""
        import hashlib
        dest_dir = self.path.parent / "ddd_files" / hashlib.sha256(profile_key.encode()).hexdigest()[:24]
        dest_dir.mkdir(parents=True, exist_ok=True)
        stored = []
        with self._connect() as conn:
            if self.get(profile_key) is None:
                raise ValueError("Najpierw zapisz profil")
            for raw in paths:
                src = Path(raw)
                content = src.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                dest = dest_dir / (digest + ".ddd")
                for prior in self.list_archived_files(profile_key):
                    if hashlib.sha256(prior.read_bytes()).hexdigest() == digest:
                        dest = prior
                        break
                if dest.exists():
                    if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                        raise ValueError("Uszkodzona kopia archiwalna DDD")
                else:
                    dest.write_bytes(content)
                conn.execute("INSERT OR IGNORE INTO archived_files (profile_key, stored_path, original_name) VALUES (?, ?, ?)",
                             (profile_key, str(dest), src.name))
                stored.append(dest)
        return stored

    def list_archived_files(self, profile_key: str) -> list[Path]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT stored_path FROM archived_files WHERE profile_key = ? "
                "ORDER BY id",
                (profile_key,),
            ).fetchall()
        out: list[Path] = []
        for row in rows:
            path = Path(str(row["stored_path"]))
            if path.is_file():
                out.append(path)
        return out
