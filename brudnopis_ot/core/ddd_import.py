"""Transactional DDD imports with content identity and conservative source refresh."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from brudnopis_ot.core.ddd_file_loader import DddLoadError, load_ddd_profiles, merge_load_into_profile, merge_open_activity


@dataclass
class ImportReport:
    imported: int = 0
    merged: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    profiles: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def text(self):
        lines = [f"Zaimportowano: {self.imported} · Scalono: {self.merged} · Pominięto: {self.skipped} · Błędy: {len(self.errors)}"]
        lines.extend(p.profile_label for p in self.profiles.values())
        lines.extend(self.errors)
        lines.extend(self.warnings)
        return "\n".join(lines)


def _snapshot(profile):
    # Includes auxiliary raw data. Manual changes to any raw field block refresh.
    from dataclasses import asdict
    data = asdict(profile)
    data.pop('title', None)
    # Recovered last-change metadata is not a manual edit of closed raw intervals.
    data.pop('open_activity', None)
    return _canonical_snapshot(json.dumps(data, default=str, sort_keys=True, ensure_ascii=False))


def _canonical_snapshot(value):
    data = json.loads(value)
    data.pop('open_activity', None)
    # Source collections describe evidence, not UI ordering. Old ledgers could
    # store parser order while SQLite returns chronological order.
    for key, items in data.items():
        if isinstance(items, list):
            data[key] = sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def _baseline_matches(baseline, profile):
    return _canonical_snapshot(baseline['snapshot']) == _snapshot(profile)


def _schema(store):
    with store._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ddd_sources (profile_key TEXT NOT NULL REFERENCES profiles(profile_key) ON DELETE CASCADE, digest TEXT NOT NULL, stored_path TEXT NOT NULL, original_name TEXT NOT NULL, PRIMARY KEY(profile_key, digest))")
        conn.execute("CREATE TABLE IF NOT EXISTS ddd_baselines (profile_key TEXT PRIMARY KEY REFERENCES profiles(profile_key) ON DELETE CASCADE, snapshot TEXT NOT NULL, complete INTEGER NOT NULL)")


def preview_profile_end(store, key, path, *, loader=load_ddd_profiles):
    """Review only: an older source must never silently shorten a newer profile."""
    old = store.get(key)
    if old is None or old.source_kind == 'template':
        raise DddLoadError('Wybierz profil DDD.')
    parts = [p for p in loader(path) if p.profile_key == key and p.segments]
    if len(parts) != 1:
        raise DddLoadError('Plik nie zawiera pewnych aktywności wybranego kierowcy.')
    cutoff = max(datetime.combine(s.day, datetime.min.time()) + timedelta(minutes=s.end_minute) for s in parts[0].segments)
    kept = []
    removed = 0
    for s in old.segments:
        limit = int((cutoff - datetime.combine(s.day, datetime.min.time())).total_seconds() // 60)
        end = min(s.end_minute, max(s.start_minute, limit))
        removed += s.end_minute - end
        if end > s.start_minute:
            kept.append(replace(s, end_minute=end))
    candidate = replace(old, segments=kept,
                        date_from=min((s.day for s in kept), default=None),
                        date_to=max((s.day for s in kept), default=None))
    return old, candidate, cutoff, removed


def apply_profile_end(store, original, candidate):
    """Apply an explicitly confirmed preview, retaining a database backup."""
    _schema(store)
    with store._connect() as conn:
        if _snapshot(store.get(original.profile_key)) != _snapshot(original):
            raise DddLoadError('Profil zmienił się od podglądu. Sprawdź koniec ponownie.')
        conn.execute('CREATE TABLE IF NOT EXISTS ddd_profile_backups (id INTEGER PRIMARY KEY, profile_key TEXT NOT NULL, created_at TEXT NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL)')
        backup = json.loads(_snapshot(original))
        backup['title'] = original.title
        conn.execute('INSERT INTO ddd_profile_backups (profile_key, created_at, reason, snapshot) VALUES (?, ?, ?, ?)',
                     (original.profile_key, datetime.now(timezone.utc).isoformat(), 'confirmed_recording_end', json.dumps(backup, ensure_ascii=False)))
        saved = store.upsert(candidate)
        # This is an explicit correction; a rebuild must not restore old tails.
        conn.execute('INSERT OR REPLACE INTO ddd_baselines VALUES (?, ?, 0)', (saved.profile_key, _snapshot(saved)))
        return saved


def _merge_legacy(old, incoming, issues=None):
    """Add uncovered intervals only; never replace unverified historical data."""
    from brudnopis_ot.core.ddd_file_loader import replace_range
    from brudnopis_ot.core.hudonizator_reader import RawSegment
    issues = issues if issues is not None else []
    by_day = {}
    for s in old.segments:
        by_day.setdefault(s.day, []).append(s)
    additions = []
    conflicts = set()
    for s in sorted(incoming.segments, key=lambda s: (s.day, s.start_minute, s.end_minute)):
        midnight = datetime.combine(s.day, datetime.min.time())
        if s.activity not in {'drive', 'work', 'rest', 'availability'} or not 0 <= s.start_minute < s.end_minute <= 1440:
            a, b = max(0, min(1439, s.start_minute)), max(1, min(1440, s.end_minute))
            issues.append((midnight + timedelta(minutes=a), midnight + timedelta(minutes=max(a + 1, b)),
                           'Niepoprawny lub nieznany zapis DDD; nie przypisano aktywności.'))
            continue
        remaining = [(s.start_minute, s.end_minute)]
        for previous in by_day.get(s.day, []):
            start = max(s.start_minute, previous.start_minute)
            end = min(s.end_minute, previous.end_minute)
            if start < end and s.activity != previous.activity:
                conflicts.update((s.day, minute) for minute in range(start, end))
                issues.append((midnight + timedelta(minutes=start), midnight + timedelta(minutes=end),
                               f'Sprzeczne zapisy DDD: {previous.activity} / {s.activity}. Zachowano wcześniejszy zapis.'))
            pieces = []
            for a, b in remaining:
                if previous.end_minute <= a or previous.start_minute >= b:
                    pieces.append((a, b))
                else:
                    if a < previous.start_minute:
                        pieces.append((a, previous.start_minute))
                    if previous.end_minute < b:
                        pieces.append((previous.end_minute, b))
            remaining = pieces
        added = [RawSegment(s.day, s.activity, a, b) for a, b in remaining]
        additions.extend(added)
        by_day.setdefault(s.day, []).extend(added)
    merged = merge_load_into_profile(old, replace(incoming, segments=additions))
    # Auxiliary records can also have manual changes. Retain old records at
    # known timestamps instead of replacing the entire incoming date range.
    auxiliary = {}
    for field_name in ('border_crossings', 'places', 'gnss_points'):
        existing = getattr(old, field_name)
        known = {r.occurred_at for r in existing}
        added = tuple(r for r in getattr(incoming, field_name) if r.occurred_at not in known)
        auxiliary[field_name] = tuple(sorted(set((*existing, *added)), key=lambda r: r.occurred_at))
    return replace(merged, title=old.title, **auxiliary), len(conflicts)


def import_files(store, paths, *, loader=load_ddd_profiles):
    """One transaction per file, including all its driver profiles and archive rows."""
    _schema(store)
    report = ImportReport()
    for raw in paths:
        path = Path(raw)
        try:
            digest = sha256(path.read_bytes()).hexdigest()
            parts = loader(path)
            if not parts:
                raise DddLoadError("Brak profili kierowców w pliku")
            if len({p.profile_key for p in parts}) != len(parts):
                raise DddLoadError("Powielona tożsamość profilu w pliku")
            saved_profiles = {}
            new_count = 0
            processed = 0
            warnings = []
            with store._connect() as conn:
                for part in parts:
                    if part.source_kind == 'template' or not part.card_number or not (part.segments or part.open_activity):
                        raise DddLoadError("Brak pewnej tożsamości kierowcy lub aktywności")
                    key = part.profile_key
                    old = store.get(key)
                    prior = conn.execute("SELECT * FROM ddd_sources WHERE profile_key=? AND digest=?", (key, digest)).fetchone()
                    if prior:
                        archive = Path(prior['stored_path'])
                        if not archive.is_file() or sha256(archive.read_bytes()).hexdigest() != digest:
                            raise DddLoadError("Brak lub uszkodzenie archiwum. Użyj odświeżenia po przywróceniu pliku źródłowego.")
                        if old is None:
                            raise DddLoadError('Brak profilu dla zarejestrowanego źródła DDD.')
                        pending = merge_open_activity(old.open_activity, part.open_activity, old.segments)
                        store.set_open_activity(key, pending)
                        saved_profiles[key] = store.get(key)
                        continue
                    baseline = conn.execute("SELECT * FROM ddd_baselines WHERE profile_key=?", (key,)).fetchone()
                    manual_raw = bool(old and baseline and not _baseline_matches(baseline, old))
                    if manual_raw:
                        # Migrate historical raw activity edits into the separate
                        # scenario layer before adding new source evidence.
                        from datetime import date
                        from brudnopis_ot.core.hudonizator_reader import RawSegment
                        prior_segments = [RawSegment(date.fromisoformat(s['day']), s['activity'], s['start_minute'], s['end_minute'])
                                          for s in json.loads(baseline['snapshot'])['segments']]
                        edited = store.load_ddd_scenario(key)
                        if prior_segments != old.segments:
                            old = store.upsert(replace(old, segments=prior_segments))
                            store.save_ddd_scenario(key, edited)
                        warnings.append(f'{path.name}: zachowano ręczne korekty profilu; dodano nowe dane DDD.')
                    part = replace(part, filenames=(path.name,))
                    issues = []
                    empty = replace(part, segments=[], filenames=(), border_crossings=(), places=(), gnss_points=())
                    merged, conflicts = _merge_legacy(old or empty, part, issues)
                    if not merged.segments and merged.open_activity is None:
                        raise DddLoadError('Brak poprawnych segmentów aktywności w pliku.')
                    saved = store.upsert(merged)
                    for start, end, reason in issues:
                        store.add_data_issue(key, start, end, f'{path.name}: {reason}')
                    if issues:
                        warnings.append(f"{path.name}: różnice aktywności obejmują {conflicts} min; niejasne okresy oznaczono ! na osi. Zachowano wcześniejsze bloki i dodano brakujące okresy. Plik zarchiwizowano.")
                    archived = store.archive_source_files(key, [path])[0]
                    if sha256(archived.read_bytes()).hexdigest() != digest:
                        raise DddLoadError("Plik zmienił się w trakcie importu")
                    conn.execute("INSERT INTO ddd_sources VALUES (?, ?, ?, ?)", (key, digest, str(archived), path.name))
                    complete = baseline['complete'] if baseline else int(old is None)
                    if manual_raw or issues:
                        complete = 0
                    conn.execute("INSERT OR REPLACE INTO ddd_baselines VALUES (?, ?, ?)", (key, _snapshot(saved), complete))
                    saved_profiles[key] = saved
                    new_count += int(old is None)
                    processed += 1
            report.profiles.update(saved_profiles)
            report.warnings.extend(warnings)
            if not processed:
                report.skipped += 1
            elif new_count:
                report.imported += 1
            else:
                report.merged += 1
        except Exception as exc:
            report.errors.append(f"{path.name}: {exc}")
    return report


def recover_open_activity(store, key, *, loader=load_ddd_profiles):
    """Recover only last-change metadata from hash-verified archives of old profiles."""
    old = store.get(key)
    if old is None or old.source_kind != 'card':
        return old
    with store._connect() as conn:
        checked = conn.execute('SELECT open_activity_checked FROM profiles WHERE profile_key=?', (key,)).fetchone()[0]
        if checked:
            return old
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ddd_sources'").fetchone():
            return old
        rows = conn.execute('SELECT * FROM ddd_sources WHERE profile_key=? ORDER BY rowid DESC', (key,)).fetchall()
        for row in rows:
            path = Path(row['stored_path'])
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != row['digest']:
                continue
            try:
                parts = [p for p in loader(path) if p.profile_key == key]
            except (DddLoadError, ValueError, OSError):
                # A failed optional metadata recovery must not hide the saved profile.
                continue
            if len(parts) != 1:
                continue
            part = parts[0]
            if old.date_to and (part.date_to is None or part.date_to < old.date_to):
                continue
            pending = merge_open_activity(old.open_activity, part.open_activity, old.segments)
            if pending is not None or (part.segments and old.segments and
                    max((s.day, s.end_minute) for s in part.segments) >= max((s.day, s.end_minute) for s in old.segments)):
                store.set_open_activity(key, pending)
                return store.get(key)
    return old


def refresh_profile(store, key, *, loader=load_ddd_profiles):
    """Rebuild only verified source ledger of the selected, unchanged profile.

    Older schemas have no source ownership or edit history: a refusal is safer
    than converting historical REST/WORK based on today's decoder output.
    """
    _schema(store)
    with store._connect() as conn:
        old = store.get(key)
        baseline = conn.execute("SELECT * FROM ddd_baselines WHERE profile_key=?", (key,)).fetchone()
        rows = conn.execute("SELECT * FROM ddd_sources WHERE profile_key=? ORDER BY rowid", (key,)).fetchall()
        if old is None or old.source_kind == 'template':
            raise DddLoadError("Wybierz profil zaimportowany z DDD")
        if not baseline or not baseline['complete'] or not rows:
            archives = store.list_archived_files(key)
            detail = "Są pliki archiwalne, ale brakuje historii źródeł i ręcznych korekt." if archives else "Brak dostępnych plików archiwalnych."
            raise DddLoadError(detail + " Nie można bezpiecznie przeliczyć starego profilu. Zachowano wszystkie dane; nie przemapowano rest/work.")
        if not _baseline_matches(baseline, old):
            raise DddLoadError("Wykryto ręczne zmiany danych. Odświeżenie przerwane, korekty zachowane.")
        rebuilt = None
        for row in rows:
            path = Path(row['stored_path'])
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != row['digest']:
                raise DddLoadError(f"Brak lub zmieniona zawartość archiwum: {row['original_name']}. Zachowano profil.")
            parts = [p for p in loader(path) if p.profile_key == key]
            if len(parts) != 1:
                raise DddLoadError(f"Niepewne przypisanie źródła {row['original_name']} do tego samego kierowcy. Zachowano profil.")
            part = replace(parts[0], filenames=(row['original_name'],))
            rebuilt = merge_load_into_profile(rebuilt, part)
        rebuilt = replace(rebuilt, title=old.title)
        saved = store.upsert(rebuilt)
        conn.execute("UPDATE ddd_baselines SET snapshot=? WHERE profile_key=?", (_snapshot(saved), key))
        return saved
