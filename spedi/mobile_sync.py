"""One-way publication: modified Brudnopis -> Firestore. Never imports phone data."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from brudnopis_ot.core.ddd_store import DddProfileStore
from spedi.mobile_schedule import build_driver_documents


@dataclass
class MobileSyncResult:
    count: int = 0
    message: str = ""


def published_document(source):
    # Android needs deadlines, not a second copy of the entire DDD timeline.
    return {**{k: v for k, v in source.items() if k != "scenario"},
            "active": True, "sync_direction": "desktop_to_phone"}


def sync_with_client(store, user):
    from google.cloud import firestore
    result = MobileSyncResult()
    schedules = user.collection("driver_schedules")
    local = build_driver_documents(store)  # Read only; do not load mobile plans.
    remote = {snap.id: snap.to_dict() for snap in schedules.stream(timeout=30)}
    for identity, source in local.items():
        published = published_document(source)
        if len(json.dumps(published, ensure_ascii=False).encode()) > 850_000:
            raise ValueError(f'Profil {source["display_name"]}: za duże dane dla Firebase.')
        known = {k: v for k, v in (remote.get(identity) or {}).items() if k != "synced_at"}
        if known == published:
            result.count += 1
            continue  # Avoid a second read of every unchanged driver on Spark.
        ref = schedules.document(identity)

        @firestore.transactional
        def publish(transaction):
            current = ref.get(transaction=transaction).to_dict()
            compare = {k: v for k, v in (current or {}).items() if k != "synced_at"}
            if compare != published:
                transaction.set(ref, {**published, "synced_at": datetime.now(timezone.utc)})

        publish(user._client.transaction())
        result.count += 1
    # Retire missing profiles only after the complete source list was published.
    for identity, cloud in remote.items():
        if identity not in local and cloud.get("active", True):
            ref = schedules.document(identity)

            @firestore.transactional
            def retire(transaction):
                current = ref.get(transaction=transaction).to_dict()
                if current and current.get("active", True):
                    transaction.update(ref, {"active": False, "synced_at": datetime.now(timezone.utc)})

            retire(user._client.transaction())
    result.message = f'Wysłano terminy {result.count} kierowców. Brudnopis → telefon.'
    return result


def sync_now(path):
    from markus_dispatcher.integrations import firebase_sync
    from firebase_admin import firestore
    setup = firebase_sync.load_setup()
    if not setup.configured:
        raise RuntimeError(setup.missing_reason)
    app = firebase_sync._init_app(setup.service_account)
    client = firestore.client(app=app)
    return sync_with_client(DddProfileStore(path), client.collection("users").document(setup.uid))
