"""Sync MarkusDispatcher ↔ Firebase Firestore (+ Storage dla dokumentów)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from markus_dispatcher import config
from markus_dispatcher.core.models import JobData
from markus_dispatcher.data import db

log = logging.getLogger(__name__)

META_UID = "firebase_uid"
META_LAST_SYNC = "firebase_last_sync_at"

_app = None


@dataclass
class SyncResult:
    ok: bool
    message: str
    pushed_drivers: int = 0
    pushed_jobs: int = 0
    pushed_clients: int = 0
    pulled_drivers: int = 0
    pulled_jobs: int = 0
    pulled_clients: int = 0
    files_up: int = 0
    files_down: int = 0


@dataclass
class FirebaseSetup:
    uid: str
    service_account: Path
    configured: bool

    @property
    def missing_reason(self) -> str:
        if not self.service_account.exists():
            return (
                f"Brak service-account.json.\n\n"
                f"Zapisz klucz z Firebase Console tutaj:\n{self.service_account}"
            )
        if not self.uid.strip():
            return (
                "Brak UID użytkownika Firebase.\n"
                "Authentication → Users → skopiuj User UID."
            )
        return ""


def ensure_firebase_dir() -> Path:
    config.FIREBASE_DIR.mkdir(parents=True, exist_ok=True)
    return config.FIREBASE_DIR


def load_setup() -> FirebaseSetup:
    ensure_firebase_dir()
    uid = ""
    if config.FIREBASE_CONFIG.exists():
        try:
            raw = json.loads(config.FIREBASE_CONFIG.read_text(encoding="utf-8"))
            uid = str(raw.get("uid") or "").strip()
        except (OSError, json.JSONDecodeError, TypeError):
            uid = ""
    if not uid:
        uid = (db.get_meta(META_UID) or "").strip()
    return FirebaseSetup(
        uid=uid,
        service_account=config.FIREBASE_SERVICE_ACCOUNT,
        configured=bool(uid and config.FIREBASE_SERVICE_ACCOUNT.exists()),
    )


def save_uid(uid: str) -> None:
    uid = uid.strip()
    ensure_firebase_dir()
    payload = {"uid": uid}
    if config.FIREBASE_CONFIG.exists():
        try:
            existing = json.loads(
                config.FIREBASE_CONFIG.read_text(encoding="utf-8")
            )
            if isinstance(existing, dict):
                existing["uid"] = uid
                payload = existing
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    config.FIREBASE_CONFIG.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    db.set_meta(META_UID, uid)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime:
    text = (value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    text = text.replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_newer(remote: str, local: str) -> bool:
    return _parse_ts(remote) > _parse_ts(local)


def _job_doc(job: JobData) -> dict[str, Any]:
    if job.local_only:
        raise ValueError('Zlecenie z lokalnego importu nie może być wysłane do Firebase.')
    updated = job.updated_at or db.get_job_updated_at(job.id or 0) or _now_iso()
    return {
        "local_id": job.id,
        "ref_numbers": job.ref_numbers,
        "status": job.status,
        "driver_id": job.driver_id,
        "client_id": job.client_id,
        "client_name": job.client_name,
        "contact_phone": job.contact_phone,
        "contact_email": job.contact_email,
        "client_message": job.client_message,
        "load_address": job.load_address,
        "load_datetime": job.load_datetime,
        "load_lat": job.load_coords.lat if job.load_coords else None,
        "load_lon": job.load_coords.lon if job.load_coords else None,
        "unload_address": job.unload_address,
        "unload_datetime": job.unload_datetime,
        "unload_lat": job.unload_coords.lat if job.unload_coords else None,
        "unload_lon": job.unload_coords.lon if job.unload_coords else None,
        "pallets": job.pallets,
        "notes": job.notes,
        "maps_url": job.maps_url,
        "archive_path": job.archive_path,
        "updated_at": updated,
    }


def _init_app(service_account: Path):
    global _app
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError as exc:
        raise RuntimeError(
            "Brak pakietu firebase-admin.\n"
            "W venv: pip install -e \".[sync]\""
        ) from exc

    if _app is not None:
        return _app
    if firebase_admin._apps:
        _app = firebase_admin.get_app()
        return _app
    cred = credentials.Certificate(str(service_account))
    _app = firebase_admin.initialize_app(
        cred,
        {"storageBucket": _guess_bucket(service_account)},
    )
    return _app


def _project_id(service_account: Path) -> str:
    try:
        data = json.loads(service_account.read_text(encoding="utf-8"))
        return str(data.get("project_id") or "").strip()
    except (OSError, json.JSONDecodeError, TypeError):
        return ""


def _configured_bucket() -> str:
    if not config.FIREBASE_CONFIG.exists():
        return ""
    try:
        raw = json.loads(config.FIREBASE_CONFIG.read_text(encoding="utf-8"))
        return str(raw.get("storageBucket") or "").strip()
    except (OSError, json.JSONDecodeError, TypeError):
        return ""


def _bucket_names(service_account: Path) -> list[str]:
    """Nowe projekty Firebase mają `{id}.firebasestorage.app`, stare `{id}.appspot.com`."""
    names: list[str] = []
    configured = _configured_bucket()
    if configured:
        names.append(configured)
    project = _project_id(service_account)
    if project:
        names.append(f"{project}.firebasestorage.app")
        names.append(f"{project}.appspot.com")
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def _guess_bucket(service_account: Path) -> str | None:
    names = _bucket_names(service_account)
    return names[0] if names else None


def _open_storage_bucket(storage_mod, service_account: Path):
    last_exc: Exception | None = None
    for name in _bucket_names(service_account):
        bucket = storage_mod.bucket(name)
        try:
            bucket.blob("__spedi_bucket_probe__").exists()
            return bucket
        except Exception as exc:  # noqa: BLE001 — 404 = zły bucket, próbuj następny
            last_exc = exc
            text = str(exc).lower()
            if "does not exist" in text or "404" in text or "not found" in text:
                log.warning("storage bucket %s niedostępny: %s", name, exc)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return storage_mod.bucket()


def sync_now(*, upload_files: bool = True) -> SyncResult:
    setup = load_setup()
    if not setup.configured:
        return SyncResult(ok=False, message=setup.missing_reason)

    try:
        _init_app(setup.service_account)
        from firebase_admin import firestore, storage
    except Exception as exc:  # noqa: BLE001 — komunikat dla UI
        return SyncResult(ok=False, message=f"Firebase init: {exc}")

    uid = setup.uid
    fs = firestore.client()
    root = fs.collection("users").document(uid)
    result = SyncResult(ok=True, message="")

    try:
        result.pushed_drivers = _push_drivers(root)
        result.pushed_clients = _push_clients(root)
        result.pushed_jobs = _push_jobs(root)
        result.pulled_drivers = _pull_drivers(root)
        result.pulled_clients = _pull_clients(root)
        result.pulled_jobs = _pull_jobs(root)
        if upload_files:
            up, down = _sync_files(root, uid, storage)
            result.files_up, result.files_down = up, down
        stamp = _now_iso()
        root.collection("meta").document("sync").set(
            {
                "last_sync_at": stamp,
                "device": "desktop",
            },
            merge=True,
        )
        db.set_meta(META_LAST_SYNC, stamp)
    except Exception as exc:  # noqa: BLE001
        log.exception("firebase sync failed")
        return SyncResult(ok=False, message=str(exc))

    result.message = (
        f"Sync OK · push D/J/C {result.pushed_drivers}/"
        f"{result.pushed_jobs}/{result.pushed_clients} · "
        f"pull {result.pulled_drivers}/{result.pulled_jobs}/"
        f"{result.pulled_clients}"
        + (
            f" · pliki ↑{result.files_up} ↓{result.files_down}"
            if upload_files
            else ""
        )
    )
    return result


def _push_drivers(root) -> int:
    col = root.collection("drivers")
    n = 0
    for driver in db.list_drivers(active_only=False):
        col.document(str(driver.id)).set(
            {
                "local_id": driver.id,
                "display_name": driver.display_name,
                "is_active": driver.is_active,
                "status": driver.status,
                "note": driver.note,
                "note_fixed": driver.note_fixed,
                "updated_at": _now_iso(),
            },
            merge=True,
        )
        n += 1
    return n


def _push_clients(root) -> int:
    col = root.collection("clients")
    n = 0
    for client in db.list_clients():
        col.document(str(client.id)).set(
            {
                "local_id": client.id,
                "name": client.name,
                "email": client.email,
                "phone": client.phone,
                "preferred_channel": client.preferred_channel,
                "notes": client.notes,
                "updated_at": _now_iso(),
            },
            merge=True,
        )
        n += 1
    return n


def _push_jobs(root) -> int:
    col = root.collection("jobs")
    n = 0
    for job in db.list_jobs(include_done=True):
        if job.local_only or job.id is None:
            continue
        remote = col.document(str(job.id)).get()
        local_ts = job.updated_at or db.get_job_updated_at(job.id)
        if remote.exists:
            remote_ts = str((remote.to_dict() or {}).get("updated_at") or "")
            if _is_newer(remote_ts, local_ts):
                continue
        col.document(str(job.id)).set(_job_doc(job), merge=True)
        n += 1
    return n


def _pull_drivers(root) -> int:
    n = 0
    for snap in root.collection("drivers").stream():
        data = snap.to_dict() or {}
        try:
            local_id = int(data.get("local_id") or snap.id)
        except (TypeError, ValueError):
            continue
        existing = db.get_driver(local_id)
        if existing is not None:
            # Desktop jest źródłem prawdy dla floty w v1 — nie nadpisuj lokalnych.
            continue
        db.upsert_driver_by_id(
            local_id,
            display_name=str(data.get("display_name") or f"Kierowca {local_id}"),
            is_active=bool(data.get("is_active", True)),
            status=str(data.get("status") or ""),  # type: ignore[arg-type]
            note=str(data.get("note") or ""),
            note_fixed=str(data.get("note_fixed") or ""),
        )
        n += 1
    return n


def _pull_clients(root) -> int:
    n = 0
    for snap in root.collection("clients").stream():
        data = snap.to_dict() or {}
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        try:
            local_id = int(data.get("local_id") or snap.id)
        except (TypeError, ValueError):
            local_id = None
        existing = None
        if local_id is not None:
            for c in db.list_clients():
                if c.id == local_id:
                    existing = c
                    break
        if existing is not None:
            continue
        if any(c.name.casefold() == name.casefold() for c in db.list_clients()):
            continue
        db.upsert_client(
            name=name,
            email=str(data.get("email") or ""),
            phone=str(data.get("phone") or ""),
            preferred_channel=str(data.get("preferred_channel") or "both"),  # type: ignore[arg-type]
            notes=str(data.get("notes") or ""),
            client_id=None,
        )
        n += 1
    return n


def _pull_jobs(root) -> int:
    n = 0
    for snap in root.collection("jobs").stream():
        data = snap.to_dict() or {}
        try:
            local_id = int(data.get("local_id") or snap.id)
        except (TypeError, ValueError):
            local_id = None
        remote_ts = str(data.get("updated_at") or "")
        if local_id is not None:
            local = db.get_job(local_id)
            if local is not None:
                if local.local_only:
                    continue
                local_ts = local.updated_at or db.get_job_updated_at(local_id)
                if not _is_newer(remote_ts, local_ts):
                    continue
        db.apply_remote_job(data, local_id=local_id)
        n += 1
    return n


def _remember_bucket(name: str) -> None:
    if not name:
        return
    ensure_firebase_dir()
    payload: dict[str, Any] = {}
    if config.FIREBASE_CONFIG.exists():
        try:
            existing = json.loads(config.FIREBASE_CONFIG.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = existing
        except (OSError, json.JSONDecodeError, TypeError):
            payload = {}
    if payload.get("storageBucket") == name:
        return
    payload["storageBucket"] = name
    config.FIREBASE_CONFIG.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _sync_files(root, uid: str, storage_mod) -> tuple[int, int]:
    """Upload brakujących plików z archive_path; download stub v1 (tylko upload)."""
    up = 0
    try:
        bucket = _open_storage_bucket(storage_mod, config.FIREBASE_SERVICE_ACCOUNT)
        _remember_bucket(getattr(bucket, "name", "") or "")
    except Exception:  # noqa: BLE001
        log.exception("nie udało się otworzyć Firebase Storage")
        return 0, 0

    for job in db.list_jobs(include_done=True):
        if job.local_only or job.id is None or not job.archive_path:
            continue
        local_dir = config.ARCHIVE_ROOT / job.archive_path / "dokumenty"
        if not local_dir.is_dir():
            continue
        for path in local_dir.iterdir():
            if not path.is_file():
                continue
            blob_path = f"users/{uid}/jobs/{job.id}/dokumenty/{path.name}"
            blob = bucket.blob(blob_path)
            try:
                if blob.exists():
                    continue
            except Exception:  # noqa: BLE001
                pass
            try:
                blob.upload_from_filename(str(path))
                up += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("upload %s failed: %s", path.name, exc)
    return up, 0
