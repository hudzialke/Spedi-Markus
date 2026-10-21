"""One-way sync regression: even hostile/legacy phone data cannot modify SQLite."""
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib

from brudnopis_ot.core.ddd_store import DddProfileStore
from spedi.mobile_schedule import build_driver_documents, driver_document_id
from spedi.mobile_sync import published_document, sync_with_client


class Snapshot:
    def __init__(self, ref):
        self.id = ref.id
        self.value = deepcopy(ref.db.data.get(ref.path))
    def to_dict(self): return deepcopy(self.value)

class Ref:
    def __init__(self, db, path): self.db, self.path, self.id = db, path, path.split("/")[-1]
    def collection(self, name): return Collection(self.db, self.path + "/" + name)
    @property
    def _client(self): return self.db
    def get(self, transaction=None): return Snapshot(self)
    def set(self, value):
        self.db.writes += 1
        self.db.data[self.path] = deepcopy(value)

class Collection:
    def __init__(self, db, path): self.db, self.path = db, path
    def document(self, name): return Ref(self.db, self.path + "/" + name)
    def stream(self, **kwargs):
        return [Snapshot(Ref(self.db, path)) for path in list(self.db.data)
                if path.startswith(self.path + "/") and "/" not in path[len(self.path)+1:]]

class Tx:
    def __init__(self, db): self.db = db
    def set(self, ref, value): ref.set(value)
    def update(self, ref, value):
        self.db.writes += 1
        self.db.data[ref.path].update(deepcopy(value))

class Db:
    def __init__(self): self.data, self.writes = {}, 0
    def collection(self, name): return Collection(self, name)
    def transaction(self): return Tx(self)


def test_publication_drops_full_timeline_and_keeps_engine_dates():
    from tests.test_mobile_schedule import pair_scenario, schedule
    source = schedule(pair_scenario())
    result = published_document(source)
    assert "scenario" not in result and "source" not in result and "plan" not in result
    assert result["rest_start_by"] == source["rest_start_by"]
    assert result["pending_compensations"] == source["pending_compensations"]
    assert result["sync_direction"] == "desktop_to_phone"


def test_phone_data_never_changes_sqlite_and_sync_is_idempotent(monkeypatch, tmp_path):
    from google.cloud import firestore
    monkeypatch.setattr(firestore, "transactional", lambda fn: lambda transaction: fn(transaction))
    store = DddProfileStore(tmp_path / "profiles.sqlite")
    profile = store.create_template("Jan", date(2026, 9, 21))
    source = build_driver_documents(store)[driver_document_id(profile.profile_key)]
    db = Db(); user = db.collection("users").document("owner")
    ref = user.collection("driver_schedules").document(driver_document_id(profile.profile_key))
    # A legacy edited document, plus an old mobile-plan journal, must be ignored.
    ref.set({**published_document(source), "week1_monday": "2026-09-28",
             "plan": {"week1_monday": "2026-09-28", "departure_at": "BAD", "planned_return_at": None},
             "source": deepcopy(source), "pending_plan": {"week1_monday": "2026-10-05"}, "sync_conflict": True})
    with store._connect() as conn:
        conn.execute("CREATE TABLE mobile_driver_plans (profile_key TEXT PRIMARY KEY, plan_json TEXT)")
        conn.execute("INSERT INTO mobile_driver_plans VALUES (?, ?)", (profile.profile_key, '{"week1_monday":"2026-09-28"}'))
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    result = sync_with_client(store, user)
    assert result.count == 1
    assert store.get_cycle_start(profile.profile_key) == date(2026, 9, 21)
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before
    actual = ref.get().to_dict()
    assert actual["week1_monday"] == "2026-09-21"
    assert "plan" not in actual and "sync_conflict" not in actual
    count = db.writes
    sync_with_client(store, user)
    assert db.writes == count


def test_desktop_edits_publish_and_deleted_profile_retires_without_touching_jobs(monkeypatch, tmp_path):
    from google.cloud import firestore
    monkeypatch.setattr(firestore, "transactional", lambda fn: lambda transaction: fn(transaction))
    store = DddProfileStore(tmp_path / "profiles.sqlite")
    profile = store.create_template("Jan", date(2026, 9, 21))
    db = Db(); user = db.collection("users").document("owner")
    job = user.collection("jobs").document("job"); job.set({"ref": "A"})
    sync_with_client(store, user)
    ref = user.collection("driver_schedules").document(driver_document_id(profile.profile_key))
    old = ref.get().to_dict()
    sc = store.load_template_scenario(profile.profile_key); sc.week_start = date(2026, 9, 28)
    store.save_template_scenario(profile.profile_key, sc)
    sync_with_client(store, user)
    updated = ref.get().to_dict()
    assert updated["week4_monday"] == "2026-10-19"
    assert updated["revision"] != old["revision"]
    store.delete_template(profile.profile_key)
    sync_with_client(store, user)
    assert ref.get().to_dict()["active"] is False
    assert job.get().to_dict() == {"ref": "A"}
