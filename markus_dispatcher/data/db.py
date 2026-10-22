from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from markus_dispatcher import config
from markus_dispatcher.core.models import (
    Attachment,
    AttachmentKind,
    Client,
    ContactChannel,
    Coordinates,
    Driver,
    DriverStatus,
    JobData,
    JobStatus,
)


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                note_fixed TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                preferred_channel TEXT NOT NULL DEFAULT 'both',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ref_numbers TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                driver_id INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
                client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
                client_name TEXT NOT NULL DEFAULT '',
                load_address TEXT NOT NULL DEFAULT '',
                load_datetime TEXT NOT NULL DEFAULT '',
                load_lat REAL,
                load_lon REAL,
                unload_address TEXT NOT NULL DEFAULT '',
                unload_datetime TEXT NOT NULL DEFAULT '',
                unload_lat REAL,
                unload_lon REAL,
                pallets TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                maps_url TEXT NOT NULL DEFAULT '',
                archive_path TEXT NOT NULL DEFAULT '',
                contact_phone TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                client_message TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                original_name TEXT NOT NULL DEFAULT '',
                stored_name TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        _migrate(conn)
    _maybe_import_rozpiska()


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _migrate(conn: sqlite3.Connection) -> None:
    for name, declaration in (
        ('local_only', 'INTEGER NOT NULL DEFAULT 0'),
        ('pod_requirements', "TEXT NOT NULL DEFAULT ''"),
        ('import_sources', "TEXT NOT NULL DEFAULT ''"),
    ):
        if not _column_exists(conn, 'jobs', name):
            conn.execute(f'ALTER TABLE jobs ADD COLUMN {name} {declaration}')
    if not _column_exists(conn, "jobs", "archive_path"):
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN archive_path TEXT NOT NULL DEFAULT ''"
        )
    if not _column_exists(conn, "jobs", "contact_phone"):
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN contact_phone TEXT NOT NULL DEFAULT ''"
        )
    if not _column_exists(conn, "jobs", "contact_email"):
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN contact_email TEXT NOT NULL DEFAULT ''"
        )
    if not _column_exists(conn, "jobs", "client_message"):
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN client_message TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            original_name TEXT NOT NULL DEFAULT '',
            stored_name TEXT NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else str(row["value"])


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _maybe_import_rozpiska() -> None:
    if not config.ROZPISKA_DB_CANDIDATE.exists():
        return
    with get_conn() as conn:
        if _meta_get(conn, "rozpiska_imported") == "1":
            return
        count = conn.execute("SELECT COUNT(*) AS n FROM drivers").fetchone()
        if count and int(count["n"]) > 0:
            _meta_set(conn, "rozpiska_imported", "1")
            return
        try:
            src = _connect(str(config.ROZPISKA_DB_CANDIDATE))
        except sqlite3.Error:
            return
        try:
            rows = src.execute("SELECT * FROM drivers").fetchall()
        except sqlite3.Error:
            src.close()
            return
        for row in rows:
            status = row["status"] or ""
            if status not in (
                "",
                "not_ready",
                "working",
                "empty",
                "planned",
                "reload",
            ):
                status = ""
            note_fixed = ""
            keys = row.keys()
            if "note_fixed" in keys:
                note_fixed = row["note_fixed"] or ""
            conn.execute(
                """
                INSERT INTO drivers
                    (display_name, is_active, status, note, note_fixed)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["display_name"],
                    int(row["is_active"]),
                    status,
                    row["note"] or "",
                    note_fixed,
                ),
            )
        src.close()
        _meta_set(conn, "rozpiska_imported", "1")


def _row_to_driver(row: sqlite3.Row) -> Driver:
    status = row["status"] or ""
    if status not in ("", "not_ready", "working", "empty", "planned", "reload"):
        status = ""
    return Driver(
        id=row["id"],
        display_name=row["display_name"],
        is_active=bool(row["is_active"]),
        status=status,  # type: ignore[arg-type]
        note=row["note"] or "",
        note_fixed=row["note_fixed"] or "",
    )


def list_drivers(*, active_only: bool = False) -> list[Driver]:
    sql = "SELECT * FROM drivers"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY lower(display_name) ASC, id ASC"
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_driver(row) for row in rows]


def get_driver(driver_id: int) -> Driver | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM drivers WHERE id = ?", (driver_id,)
        ).fetchone()
    return None if row is None else _row_to_driver(row)


def add_driver(display_name: str) -> Driver:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO drivers (display_name) VALUES (?)",
            (display_name.strip(),),
        )
        row = conn.execute(
            "SELECT * FROM drivers WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    assert row is not None
    return _row_to_driver(row)


def delete_driver(driver_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM drivers WHERE id = ?", (driver_id,))


def set_driver_active(driver_id: int, is_active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE drivers SET is_active = ? WHERE id = ?",
            (1 if is_active else 0, driver_id),
        )


def set_driver_status(driver_id: int, status: DriverStatus) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE drivers SET status = ? WHERE id = ?",
            (status, driver_id),
        )


def set_driver_notes(driver_id: int, note: str, note_fixed: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE drivers SET note = ?, note_fixed = ? WHERE id = ?",
            (note, note_fixed, driver_id),
        )


def _row_to_client(row: sqlite3.Row) -> Client:
    channel = row["preferred_channel"] or "both"
    if channel not in ("whatsapp", "email", "both"):
        channel = "both"
    return Client(
        id=row["id"],
        name=row["name"],
        email=row["email"] or "",
        phone=row["phone"] or "",
        preferred_channel=channel,  # type: ignore[arg-type]
        notes=row["notes"] or "",
    )


def list_clients() -> list[Client]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM clients ORDER BY lower(name) ASC, id ASC"
        ).fetchall()
    return [_row_to_client(row) for row in rows]


def upsert_client(
    *,
    name: str,
    email: str = "",
    phone: str = "",
    preferred_channel: ContactChannel = "both",
    notes: str = "",
    client_id: int | None = None,
) -> Client:
    name = name.strip()
    if not name:
        raise ValueError("Nazwa klienta jest wymagana.")
    with get_conn() as conn:
        if client_id is None:
            cur = conn.execute(
                """
                INSERT INTO clients
                    (name, email, phone, preferred_channel, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, email.strip(), phone.strip(), preferred_channel, notes),
            )
            cid = cur.lastrowid
        else:
            conn.execute(
                """
                UPDATE clients
                SET name = ?, email = ?, phone = ?,
                    preferred_channel = ?, notes = ?
                WHERE id = ?
                """,
                (
                    name,
                    email.strip(),
                    phone.strip(),
                    preferred_channel,
                    notes,
                    client_id,
                ),
            )
            cid = client_id
        row = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (cid,)
        ).fetchone()
    assert row is not None
    return _row_to_client(row)


def find_or_create_client_by_name(name: str) -> Client | None:
    cleaned = name.strip()
    if not cleaned:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM clients WHERE lower(name) = lower(?) LIMIT 1",
            (cleaned,),
        ).fetchone()
        if row is not None:
            return _row_to_client(row)
    return upsert_client(name=cleaned)


def delete_client(client_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))


def _coords(lat: object, lon: object) -> Coordinates | None:
    if lat is None or lon is None:
        return None
    return Coordinates(lat=float(lat), lon=float(lon))


def _row_to_job(row: sqlite3.Row) -> JobData:
    status = row["status"] or "draft"
    if status not in (
        "draft",
        "assigned",
        "loading",
        "en_route",
        "unloading",
        "done",
        "cancelled",
    ):
        status = "draft"
    return JobData(
        id=row["id"],
        ref_numbers=row["ref_numbers"] or "",
        status=status,  # type: ignore[arg-type]
        driver_id=row["driver_id"],
        client_id=row["client_id"],
        client_name=row["client_name"] or "",
        load_address=row["load_address"] or "",
        load_datetime=row["load_datetime"] or "",
        load_coords=_coords(row["load_lat"], row["load_lon"]),
        unload_address=row["unload_address"] or "",
        unload_datetime=row["unload_datetime"] or "",
        unload_coords=_coords(row["unload_lat"], row["unload_lon"]),
        pallets=row["pallets"] or "",
        notes=row["notes"] or "",
        maps_url=row["maps_url"] or "",
        contact_phone=_row_get(row, "contact_phone", ""),
        contact_email=_row_get(row, "contact_email", ""),
        client_message=_row_get(row, "client_message", ""),
        archive_path=_row_get(row, "archive_path", ""),
        updated_at=_row_get(row, "updated_at", ""),
        local_only=bool(row['local_only']) if 'local_only' in row.keys() else False,
        pod_requirements=_row_get(row, 'pod_requirements'),
        import_sources=_row_get(row, 'import_sources'),
    )


def _row_get(row: sqlite3.Row, key: str, default: str = "") -> str:
    if key in row.keys():
        return row[key] or default
    return default


def list_jobs(*, include_done: bool = False) -> list[JobData]:
    sql = "SELECT * FROM jobs"
    if not include_done:
        sql += " WHERE status NOT IN ('done', 'cancelled')"
    sql += " ORDER BY datetime(updated_at) DESC, id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_job(row) for row in rows]


def list_jobs_for_driver(
    driver_id: int,
    *,
    chronological: bool = True,
    include_done: bool = True,
) -> list[JobData]:
    """Lista zleceń kierowcy — chronologicznie po dacie załadunku."""
    from markus_dispatcher.core.job_archive import parse_job_stamp

    where = "WHERE driver_id = ?"
    if not include_done:
        where += " AND status NOT IN ('done', 'cancelled')"
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY id ASC",
            (driver_id,),
        ).fetchall()
    jobs = [_row_to_job(row) for row in rows]
    if chronological:
        jobs.sort(key=lambda j: (parse_job_stamp(j), j.id or 0))
    else:
        jobs.sort(key=lambda j: j.id or 0, reverse=True)
    return jobs


def set_job_archive_path(job_id: int, archive_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET archive_path = ?, updated_at = datetime('now') WHERE id = ?",
            (archive_path, job_id),
        )


def _row_to_attachment(row: sqlite3.Row) -> Attachment:
    kind = row["kind"] or "file"
    if kind not in ("pdf", "excel", "sheets", "file", "pod"):
        kind = "file"
    return Attachment(
        id=row["id"],
        job_id=row["job_id"],
        kind=kind,  # type: ignore[arg-type]
        original_name=row["original_name"] or "",
        stored_name=row["stored_name"] or "",
        source_url=row["source_url"] or "",
        created_at=row["created_at"] or "",
    )


def list_attachments(job_id: int) -> list[Attachment]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM attachments
            WHERE job_id = ?
            ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()
    return [_row_to_attachment(row) for row in rows]


def add_attachment(
    job_id: int,
    *,
    kind: AttachmentKind,
    original_name: str,
    stored_name: str,
    source_url: str = "",
) -> Attachment:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO attachments
                (job_id, kind, original_name, stored_name, source_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, kind, original_name, stored_name, source_url),
        )
        row = conn.execute(
            "SELECT * FROM attachments WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    assert row is not None
    return _row_to_attachment(row)


def delete_attachment(attachment_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM attachments WHERE id = ?", (attachment_id,))


def sync_job_to_archive(
    job: JobData,
    *,
    instruction_footer: str | None = None,
) -> JobData:
    """Po zapisie: folder kierowcy + meta/zlecenie.txt; zwraca job z archive_path."""
    from markus_dispatcher.core.job_archive import ensure_job_archive

    if job.id is None or job.driver_id is None:
        return job
    driver = get_driver(job.driver_id)
    if driver is None:
        return job
    paths = ensure_job_archive(
        driver, job, instruction_footer=instruction_footer
    )
    if job.archive_path != paths.relative:
        set_job_archive_path(job.id, paths.relative)
        job.archive_path = paths.relative
    return job

def get_job(job_id: int) -> JobData | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return None if row is None else _row_to_job(row)


def latest_job_for_driver(driver_id: int) -> JobData | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE driver_id = ? AND status NOT IN ('done', 'cancelled')
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT 1
            """,
            (driver_id,),
        ).fetchone()
    return None if row is None else _row_to_job(row)


def save_job(job: JobData) -> JobData:
    load_lat = job.load_coords.lat if job.load_coords else None
    load_lon = job.load_coords.lon if job.load_coords else None
    unload_lat = job.unload_coords.lat if job.unload_coords else None
    unload_lon = job.unload_coords.lon if job.unload_coords else None
    with get_conn() as conn:
        exists = False
        if job.id is not None:
            exists = (
                conn.execute(
                    "SELECT 1 FROM jobs WHERE id = ?", (job.id,)
                ).fetchone()
                is not None
            )
        if job.id is None or not exists:
            if job.id is None:
                cur = conn.execute(
                    """
                    INSERT INTO jobs (
                        ref_numbers, status, driver_id, client_id, client_name,
                        contact_phone, contact_email, client_message,
                        load_address, load_datetime, load_lat, load_lon,
                        unload_address, unload_datetime, unload_lat, unload_lon,
                        pallets, notes, maps_url, archive_path, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        job.ref_numbers,
                        job.status,
                        job.driver_id,
                        job.client_id,
                        job.client_name,
                        job.contact_phone,
                        job.contact_email,
                        job.client_message,
                        job.load_address,
                        job.load_datetime,
                        load_lat,
                        load_lon,
                        job.unload_address,
                        job.unload_datetime,
                        unload_lat,
                        unload_lon,
                        job.pallets,
                        job.notes,
                        job.maps_url,
                        job.archive_path,
                    ),
                )
                job_id = int(cur.lastrowid)
            else:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        id, ref_numbers, status, driver_id, client_id, client_name,
                        contact_phone, contact_email, client_message,
                        load_address, load_datetime, load_lat, load_lon,
                        unload_address, unload_datetime, unload_lat, unload_lon,
                        pallets, notes, maps_url, archive_path, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        job.id,
                        job.ref_numbers,
                        job.status,
                        job.driver_id,
                        job.client_id,
                        job.client_name,
                        job.contact_phone,
                        job.contact_email,
                        job.client_message,
                        job.load_address,
                        job.load_datetime,
                        load_lat,
                        load_lon,
                        job.unload_address,
                        job.unload_datetime,
                        unload_lat,
                        unload_lon,
                        job.pallets,
                        job.notes,
                        job.maps_url,
                        job.archive_path,
                    ),
                )
                job_id = int(job.id)
        else:
            conn.execute(
                """
                UPDATE jobs SET
                    ref_numbers = ?, status = ?, driver_id = ?, client_id = ?,
                    client_name = ?, contact_phone = ?, contact_email = ?,
                    client_message = ?,
                    load_address = ?, load_datetime = ?,
                    load_lat = ?, load_lon = ?, unload_address = ?,
                    unload_datetime = ?, unload_lat = ?, unload_lon = ?,
                    pallets = ?, notes = ?, maps_url = ?, archive_path = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    job.ref_numbers,
                    job.status,
                    job.driver_id,
                    job.client_id,
                    job.client_name,
                    job.contact_phone,
                    job.contact_email,
                    job.client_message,
                    job.load_address,
                    job.load_datetime,
                    load_lat,
                    load_lon,
                    job.unload_address,
                    job.unload_datetime,
                    unload_lat,
                    unload_lon,
                    job.pallets,
                    job.notes,
                    job.maps_url,
                    job.archive_path,
                    job.id,
                ),
            )
            job_id = job.id
        # Sticky local-only flag: a stale editor / remote payload must never
        # accidentally re-enable uploads of an imported job.
        conn.execute('UPDATE jobs SET local_only = MAX(local_only, ?), pod_requirements = ?, import_sources = ? WHERE id = ?',
                     (int(job.local_only), job.pod_requirements, job.import_sources, job_id))
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    assert row is not None
    return _row_to_job(row)

def get_meta(key: str) -> str | None:
    with get_conn() as conn:
        return _meta_get(conn, key)


def set_meta(key: str, value: str) -> None:
    with get_conn() as conn:
        _meta_set(conn, key, value)


def get_job_updated_at(job_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT updated_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return "" if row is None else str(row["updated_at"] or "")


def upsert_driver_by_id(
    driver_id: int,
    *,
    display_name: str,
    is_active: bool = True,
    status: DriverStatus = "",
    note: str = "",
    note_fixed: str = "",
) -> Driver:
    """Wstaw / nadpisz kierowcę z zachowaniem lokalnego id (sync Firebase)."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM drivers WHERE id = ?", (driver_id,)
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO drivers
                    (id, display_name, is_active, status, note, note_fixed)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    driver_id,
                    display_name.strip(),
                    1 if is_active else 0,
                    status,
                    note,
                    note_fixed,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE drivers SET
                    display_name = ?, is_active = ?, status = ?,
                    note = ?, note_fixed = ?
                WHERE id = ?
                """,
                (
                    display_name.strip(),
                    1 if is_active else 0,
                    status,
                    note,
                    note_fixed,
                    driver_id,
                ),
            )
        row = conn.execute(
            "SELECT * FROM drivers WHERE id = ?", (driver_id,)
        ).fetchone()
    assert row is not None
    return _row_to_driver(row)


def apply_remote_job(payload: dict, *, local_id: int | None) -> JobData:
    """Zastosuj dokument Firestore jako lokalne zlecenie."""
    from markus_dispatcher.core.models import Coordinates

    existing = get_job(local_id) if local_id is not None else None
    if existing is not None and existing.local_only:
        return existing

    def _coord(prefix: str) -> Coordinates | None:
        lat = payload.get(f"{prefix}_lat")
        lon = payload.get(f"{prefix}_lon")
        if lat is None or lon is None:
            return None
        try:
            return Coordinates(lat=float(lat), lon=float(lon))
        except (TypeError, ValueError):
            return None

    job = JobData(
        id=local_id,
        ref_numbers=str(payload.get("ref_numbers") or ""),
        status=str(payload.get("status") or "draft"),  # type: ignore[arg-type]
        driver_id=payload.get("driver_id"),
        client_id=payload.get("client_id"),
        client_name=str(payload.get("client_name") or ""),
        contact_phone=str(payload.get("contact_phone") or ""),
        contact_email=str(payload.get("contact_email") or ""),
        client_message=str(payload.get("client_message") or ""),
        load_address=str(payload.get("load_address") or ""),
        load_datetime=str(payload.get("load_datetime") or ""),
        load_coords=_coord("load"),
        unload_address=str(payload.get("unload_address") or ""),
        unload_datetime=str(payload.get("unload_datetime") or ""),
        unload_coords=_coord("unload"),
        pallets=str(payload.get("pallets") or ""),
        notes=str(payload.get("notes") or ""),
        maps_url=str(payload.get("maps_url") or ""),
        archive_path=str(payload.get("archive_path") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )
    saved = save_job(job)
    remote_updated = str(payload.get("updated_at") or "").strip()
    if remote_updated and saved.id is not None:
        with get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET updated_at = ? WHERE id = ?",
                (remote_updated, saved.id),
            )
        saved.updated_at = remote_updated
    return saved


def set_job_status(job_id: int, status: JobStatus) -> JobData:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs SET status = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (status, job_id),
        )
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    assert row is not None
    return _row_to_job(row)
