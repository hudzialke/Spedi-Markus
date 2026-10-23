"""Read-only background OT calculation and bounded, process-local result cache."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import Lock

from PySide6.QtCore import QThread, Signal

from brudnopis_ot.core.ddd_store import DddProfileStore
from brudnopis_ot.core.serialize import scenario_to_dict
from spedi.i18n import tr as _tr
from spedi.weekly_watch import WatchOptions, calculate, load_scenario, midnight


@dataclass(frozen=True)
class ProfileSummary:
    profile_key: str
    source_kind: str
    driver_name: str
    title: str
    card_number: str | None
    vehicle_plate: str | None


class WatchResultCache:
    def __init__(self, capacity=128):
        self.capacity = capacity
        self.entries = OrderedDict()
        self.lock = Lock()

    def get(self, key):
        with self.lock:
            value = self.entries.get(key)
            if value is not None:
                self.entries.move_to_end(key)
            return value

    def put(self, key, value):
        with self.lock:
            self.entries[key] = value
            self.entries.move_to_end(key)
            while len(self.entries) > self.capacity:
                self.entries.popitem(last=False)


RESULT_CACHE = WatchResultCache()


def input_revision(scenario, options, issues):
    # week_start, anchor, countries, plans and original/edit-rebased history all
    # affect the result. history_revision alone intentionally omits the anchor.
    payload = {'scenario': scenario_to_dict(scenario), 'options': asdict(options),
               'issues': [(start.isoformat(), end.isoformat(), reason) for start, end, reason in issues]}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class WatchTask(QThread):
    profiles_ready = Signal(int, object)
    result_ready = Signal(int, str, object)
    row_failed = Signal(int, str, str)
    failed = Signal(int, str)
    invalidated = Signal(int)

    def __init__(self, path, generation, only_selected, search, parent=None, cache=None):
        super().__init__(parent)
        self.path = Path(path).resolve()
        self.generation, self.only_selected, self.search = generation, only_selected, search
        self.cache = cache if cache is not None else RESULT_CACHE

    def run(self):
        connection = None
        try:
            # A connection is owned exclusively by this worker. No shared store's
            # _active_connection, write transaction or schema migration is used.
            connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
            connection.row_factory = sqlite3.Row
            version = connection.execute('PRAGMA data_version').fetchone()[0]
            connection.execute('BEGIN')
            profiles = [ProfileSummary(**dict(row)) for row in connection.execute(
                'SELECT profile_key, source_kind, driver_name, title, card_number, vehicle_plate '
                'FROM profiles ORDER BY source_kind, driver_name, vehicle_plate')]
            options = {row['profile_key']: WatchOptions(bool(row['selected']), row['residence_country'],
                       bool(row['international_goods']), row['verified_revision'])
                       for row in connection.execute('SELECT * FROM weekly_watch')}
            connection.commit()
            if self.isInterruptionRequested():
                return
            self.profiles_ready.emit(self.generation, [(profile, options.get(profile.profile_key, WatchOptions()))
                                                       for profile in profiles])
            store = object.__new__(DddProfileStore)
            store.path, store._active_connection = self.path, connection
            for summary in profiles:
                if self.isInterruptionRequested():
                    return
                setting = options.get(summary.profile_key, WatchOptions())
                identity = _tr('ręczny') if summary.source_kind == 'template' else (summary.card_number or summary.vehicle_plate or 'DDD')
                name = f'{summary.driver_name or summary.title} · {identity}'
                if (self.only_selected and not setting.selected) or self.search not in name.casefold():
                    continue
                try:
                    connection.execute('BEGIN')
                    profile = store.get(summary.profile_key)
                    scenario = load_scenario(store, profile)
                    issues = store.get_data_issues(summary.profile_key)
                    connection.commit()
                    if connection.execute('PRAGMA data_version').fetchone()[0] != version:
                        self.invalidated.emit(self.generation)
                        return
                    key = (str(self.path), summary.profile_key, input_revision(scenario, setting, issues))
                    result = self.cache.get(key)
                    if result is None:
                        result = calculate(scenario, setting)
                        if issues and result.history_from and any(end > midnight(result.history_from) for _, end, _ in issues):
                            result = replace(result, reduced='unknown', return_by=None,
                                return_compensation_minutes=0, return_rest_minutes=0,
                                return_reason=_tr('Powrót: rozbieżności danych DDD wymagają wyjaśnienia.'),
                                reason=_tr('Import DDD zawiera oznaczone rozbieżności danych. Sprawdź je na osi i w lupie.'))
                        self.cache.put(key, result)
                    if self.isInterruptionRequested():
                        return
                    if connection.execute('PRAGMA data_version').fetchone()[0] != version:
                        self.invalidated.emit(self.generation)
                        return
                    self.result_ready.emit(self.generation, summary.profile_key, (result, scenario, setting))
                except Exception:
                    connection.rollback()
                    self.row_failed.emit(self.generation, summary.profile_key, _tr('Nie można obliczyć profilu. Odśwież listę i sprawdź dane kierowcy.'))
        except Exception:
            self.failed.emit(self.generation, _tr('Nie można odczytać listy OT. Odśwież listę i sprawdź dostęp do bazy.'))
        finally:
            if connection is not None:
                connection.close()
