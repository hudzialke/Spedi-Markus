from __future__ import annotations

import json
from dataclasses import dataclass
from contextlib import contextmanager
import shutil
import sqlite3
import hashlib
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

from brudnopis_ot.core.ddd_file_loader import (
    BorderCrossingRecord,
    DddLoadResult,
    GnssPointRecord,
    PlaceRecord,
    SourceKind,
    _normalize_plate,
    _clean_text,
    merge_load_into_profile,
)
from brudnopis_ot.core.hudonizator_reader import RawSegment
from brudnopis_ot.core.paths import data_dir
from brudnopis_ot.core.models import Scenario


def ddd_db_path() -> Path:
    return data_dir() / "ddd_profiles.sqlite"


@dataclass(frozen=True, slots=True)
class DddProfileSummary:
    """Picker metadata; deliberately contains no activity history or GNSS points."""
    profile_key: str
    source_kind: str
    driver_name: str
    title: str
    card_number: str | None
    vehicle_plate: str | None
    date_from: date | None
    date_to: date | None

    @property
    def profile_label(self) -> str:
        if self.source_kind == 'template':
            return f'{self.driver_name} · Ręczny profil'
        if self.source_kind == 'vu' and not _clean_text(self.card_number):
            return f"VU {self.vehicle_plate or 'brak rejestracji'}"
        return f"{self.driver_name} ({self.card_number or 'brak karty'})"


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
            self._ensure_column(conn, "profiles", "open_activity_json", "TEXT")
            self._ensure_column(conn, "profiles", "open_activity_checked", "INTEGER NOT NULL DEFAULT 0")
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
                CREATE TABLE IF NOT EXISTS ddd_activity_ends (
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    start TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    end TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    PRIMARY KEY(profile_key, start, activity)
                );
                CREATE TABLE IF NOT EXISTS profile_edit_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE,
                    created_at TEXT NOT NULL, reason TEXT NOT NULL, summary TEXT NOT NULL,
                    payload BLOB NOT NULL, payload_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_profile_edit_history
                    ON profile_edit_history(profile_key, id DESC);
                """
            )

    def load_ddd_scenario(self, profile_key: str, *, original: bool = False) -> Scenario:
        from brudnopis_ot.core.profile_scenario import original_scenario, exact_scenario, rebase_scenario
        profile = self.get(profile_key)
        if profile is None or profile.source_kind == 'template':
            raise ValueError('Wybierz profil DDD.')
        source = original_scenario(profile)
        if not original:
            source = self._with_confirmed_activity_ends(profile, source)
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM ddd_scenario_edits WHERE profile_key=?', (profile_key,)).fetchone()
        if not original and row:
            return rebase_scenario(exact_scenario(json.loads(row['baseline_json'])),
                                   exact_scenario(json.loads(row['scenario_json'])), source)
        cycle = self.get_cycle_start(profile_key)
        if cycle:
            source.week_start = cycle
        return source

    def save_ddd_scenario(self, profile_key: str, scenario: Scenario, *, reason='edit') -> None:
        from brudnopis_ot.core.profile_scenario import original_scenario, exact_scenario, rebase_scenario
        from brudnopis_ot.core.serialize import scenario_to_dict
        from brudnopis_ot.core.models import copy_scenario
        profile = self.get(profile_key)
        if profile is None or profile.source_kind == 'template':
            raise ValueError('Wybierz profil DDD.')
        with self._connect() as conn:
            baseline = self._with_confirmed_activity_ends(profile, original_scenario(profile))
            row = conn.execute('SELECT baseline_json, scenario_json FROM ddd_scenario_edits WHERE profile_key=?',
                               (profile_key,)).fetchone()
            before = (rebase_scenario(exact_scenario(json.loads(row['baseline_json'])),
                                     exact_scenario(json.loads(row['scenario_json'])), baseline)
                      if row else copy_scenario(baseline))
            if not row and (cycle := self.get_cycle_start(profile_key)):
                before.week_start = cycle
            self._remember_revision(conn, profile_key, before, scenario, baseline, reason)
            conn.execute('INSERT OR REPLACE INTO ddd_scenario_edits VALUES (?, ?, ?)',
                         (profile_key, json.dumps(scenario_to_dict(baseline), ensure_ascii=False),
                          json.dumps(scenario_to_dict(scenario), ensure_ascii=False)))
            self.set_cycle_start(profile_key, scenario.week_start)

    def set_open_activity(self, profile_key, activity) -> None:
        """Metadata-only recovery never rewrites closed history or corrections."""
        payload = json.dumps({'activity': activity.activity.value, 'start': activity.start.isoformat()}) if activity else None
        with self._connect() as conn:
            conn.execute('UPDATE profiles SET open_activity_json=?, open_activity_checked=1 WHERE profile_key=?',
                         (payload, profile_key))

    def get_confirmed_activity_end(self, profile_key, activity) -> datetime | None:
        if activity is None:
            return None
        with self._connect() as conn:
            row = conn.execute('SELECT end FROM ddd_activity_ends WHERE profile_key=? AND start=? AND activity=?',
                               (profile_key, activity.start.isoformat(), activity.activity.value)).fetchone()
        return datetime.fromisoformat(row['end']) if row else None

    def confirm_activity_end(self, profile_key, expected_activity, end: datetime) -> None:
        """An explicit user supplement, separate from raw DDD and measurable totals."""
        with self._connect() as conn:
            profile = self.get(profile_key)
            if profile is None or expected_activity is None or profile.open_activity != expected_activity:
                raise ValueError('Ostatnia aktywność zmieniła się. Wczytaj profil ponownie.')
            if end.tzinfo is not None or end.second or end.microsecond or end <= expected_activity.start:
                raise ValueError('Koniec musi być późniejszy niż początek, z dokładnością do minuty (UTC).')
            conn.execute('INSERT OR REPLACE INTO ddd_activity_ends VALUES (?, ?, ?, ?, ?)',
                         (profile_key, expected_activity.start.isoformat(), expected_activity.activity.value,
                          end.isoformat(), datetime.now().isoformat(timespec='seconds')))

    def _with_confirmed_activity_ends(self, profile, scenario):
        from dataclasses import replace
        from brudnopis_ot.core.activity_ends import confirmed_segments
        from brudnopis_ot.core.ddd_blocks import segments_to_blocks
        from brudnopis_ot.core.profile_scenario import stable_id
        with self._connect() as conn:
            rows = conn.execute('SELECT start, activity, end FROM ddd_activity_ends WHERE profile_key=? ORDER BY start',
                                (profile.profile_key,)).fetchall()
        additions = segments_to_blocks(confirmed_segments(profile.segments, rows, profile.open_activity))
        for block in additions:
            block.label = 'Koniec potwierdzony przez użytkownika'
            block.id = 'confirmed_' + stable_id(block)
        return replace(scenario, blocks=sorted([*scenario.blocks, *additions], key=lambda b: (b.start, b.end)))

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

    def list_profile_summaries(self) -> list[DddProfileSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT profile_key, source_kind, driver_name, title, card_number, vehicle_plate, '
                'date_from, date_to FROM profiles ORDER BY source_kind, driver_name, vehicle_plate'
            ).fetchall()
        return [DddProfileSummary(
            row['profile_key'], row['source_kind'], row['driver_name'], row['title'],
            row['card_number'], row['vehicle_plate'],
            date.fromisoformat(row['date_from']) if row['date_from'] else None,
            date.fromisoformat(row['date_to']) if row['date_to'] else None,
        ) for row in rows]

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
        from ddd_analyzer.models import OpenActivity, ActivityType
        pending = json.loads(meta['open_activity_json']) if meta['open_activity_json'] else None
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
            open_activity=OpenActivity(ActivityType(pending['activity']), datetime.fromisoformat(pending['start'])) if pending else None,
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
            self.set_open_activity(profile.profile_key, profile.open_activity)
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

    def save_template_scenario(self, profile_key: str, scenario: Scenario, *, reason='edit') -> None:
        from brudnopis_ot.core.serialize import scenario_to_dict

        name = scenario.title.strip()
        if not name:
            raise ValueError("Podaj nazwę ręcznego profilu")
        with self._connect() as conn:
            self._remember_revision(conn, profile_key, self.load_template_scenario(profile_key), scenario, None, reason)
            cursor = conn.execute(
                "UPDATE profiles SET scenario_json = ?, cycle_start = ?, title = ?, "
                "driver_name = ?, updated_at = ? WHERE profile_key = ? AND source_kind = 'template'",
                (json.dumps(scenario_to_dict(scenario), ensure_ascii=False),
                 scenario.week_start.isoformat(), name, name, datetime.now().isoformat(), profile_key),
            )
            if cursor.rowcount != 1:
                raise ValueError("Nie znaleziono ręcznego profilu")

    def _remember_revision(self, conn, key, before, after, baseline, reason):
        from brudnopis_ot.core.serialize import scenario_to_dict
        previous, current = scenario_to_dict(before), scenario_to_dict(after)
        if previous == current:
            return
        left = {item['id']: item for item in previous['blocks']}
        right = {item['id']: item for item in current['blocks']}
        summary = {'added': len(right.keys()-left.keys()), 'removed': len(left.keys()-right.keys()),
                   'changed': sum(left[key] != right[key] for key in left.keys() & right.keys()),
                   'axis_changed': previous['week_start'] != current['week_start'],
                   'title_changed': previous['title'] != current['title']}
        data = {'scenario': previous, 'baseline': scenario_to_dict(baseline) if baseline else None}
        payload = zlib.compress(json.dumps(data, ensure_ascii=False).encode('utf-8'), level=1)
        conn.execute('INSERT INTO profile_edit_history(profile_key,created_at,reason,summary,payload,payload_hash) '
                     'VALUES (?,?,?,?,?,?)', (key, datetime.now(timezone.utc).isoformat(), reason,
                     json.dumps(summary), payload, hashlib.sha256(payload).hexdigest()))
        conn.execute('DELETE FROM profile_edit_history WHERE profile_key=? AND id NOT IN '
                     '(SELECT id FROM profile_edit_history WHERE profile_key=? ORDER BY id DESC LIMIT 25)', (key, key))

    def list_edit_history(self, profile_key):
        with self._connect() as conn:
            rows = conn.execute('SELECT id,created_at,reason,summary,length(payload) AS bytes '
                                'FROM profile_edit_history WHERE profile_key=? ORDER BY id DESC', (profile_key,)).fetchall()
        return [{**dict(row), 'summary': json.loads(row['summary'])} for row in rows]

    def restore_edit_revision(self, profile_key, revision_id):
        from brudnopis_ot.core.profile_scenario import exact_scenario, original_scenario, rebase_scenario
        with self._connect() as conn:
            row = conn.execute('SELECT payload,payload_hash FROM profile_edit_history WHERE profile_key=? AND id=?',
                               (profile_key, revision_id)).fetchone()
            if row is None or hashlib.sha256(row['payload']).hexdigest() != row['payload_hash']:
                raise ValueError('Wersja profilu nie istnieje albo jest uszkodzona.')
            decoder = zlib.decompressobj()
            raw = decoder.decompress(row['payload'], 256*1024**2+1)
            if len(raw) > 256*1024**2 or not decoder.eof:
                raise ValueError('Nieprawidłowy rozmiar historii profilu.')
            data = json.loads(raw)
            scenario = exact_scenario(data['scenario'])
            profile = self.get(profile_key)
            if profile is None:
                raise ValueError('Profil już nie istnieje.')
            if profile.source_kind == 'template':
                self.save_template_scenario(profile_key, scenario, reason='restore')
            else:
                baseline = self._with_confirmed_activity_ends(profile, original_scenario(profile))
                scenario = rebase_scenario(exact_scenario(data['baseline']), scenario, baseline)
                self.save_ddd_scenario(profile_key, scenario, reason='restore')
            return self.get(profile_key)

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
