"""Read local, selected-driver and document actions without sending messages."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from spedi.weekly_watch import WARSAW, WatchStore, calculate, load_scenario
from spedi.i18n import tr


@dataclass(frozen=True)
class ActionItem:
    identity: str
    category: str
    subject: str
    title: str
    detail: str
    deadline: datetime | None = None
    profile_key: str | None = None
    job_id: int | None = None
    target: str = 'calculation'


def urgency(item, now):
    if item.deadline is None:
        return 2, tr('Do sprawdzenia')
    due = item.deadline.replace(tzinfo=timezone.utc)
    if due < now:
        return 0, tr('Po terminie')
    if due.astimezone(WARSAW).date() == now.astimezone(WARSAW).date():
        return 1, tr('Dziś')
    return 2, tr('Wkrótce')


def collect_actions(profile_path, dispatcher_path, archive_root, *, now=None, cancelled=lambda: False):
    from brudnopis_ot.core.ddd_store import DddProfileStore
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Panel dnia wymaga czasu ze strefą.')
    now = now.astimezone(timezone.utc)
    cutoff = (now+timedelta(days=7)).replace(tzinfo=None)
    items, warnings = [], []
    if Path(profile_path).is_file():
        store = DddProfileStore(Path(profile_path))
        watch = WatchStore(store.path)
        selected = 0
        for summary in store.list_profile_summaries():
            if cancelled():
                raise InterruptedError('Anulowano odczyt panelu dnia.')
            options = watch.get(summary.profile_key)
            if not options.selected:
                continue
            selected += 1
            try:
                profile = store.get(summary.profile_key)
                result = calculate(load_scenario(store, profile), options)
                name, key = summary.profile_label, summary.profile_key
                if result.return_by and result.return_by <= cutoff:
                    items.append(ActionItem(key+'-home', 'drivers', name, tr('Powrót do domu / bazy'),
                        result.return_reason, result.return_by, key))
                if result.start_by and result.start_by <= cutoff and result.start_by != result.return_by:
                    items.append(ActionItem(key+'-rest', 'drivers', name, tr('Rozpoczęcie kolejnego OT'),
                        result.reason, result.start_by, key))
                for index, debt in enumerate(result.pending_compensations):
                    if not debt.mobility_pair and debt.deadline <= cutoff:
                        amount = f'{debt.remaining_minutes//60}:{debt.remaining_minutes%60:02d}'
                        items.append(ActionItem(f'{key}-comp-{index}', 'drivers', name,
                            tr('Termin rekompensaty')+' '+amount, result.return_reason, debt.deadline, key))
                if result.start_by is None or result.reduced == 'unknown' or result.return_by is None:
                    items.append(ActionItem(key+'-data', 'drivers', name, tr('Sprawdź kompletność historii'),
                        result.return_reason+'\n'+result.reason, profile_key=key))
                if summary.source_kind != 'template' and (summary.date_to is None or
                    (now.astimezone(WARSAW).date()-summary.date_to).days > 14):
                    items.append(ActionItem(key+'-ddd', 'drivers', name, tr('Uzupełnij świeże dane DDD'),
                        tr('Ostatni dzień danych źródłowych: ')+(str(summary.date_to) if summary.date_to else '—'), profile_key=key))
            except Exception as exc:
                warnings.append(summary.profile_label+': '+str(exc))
        if not selected:
            items.append(ActionItem('select-drivers', 'drivers', tr('Lista OT'), tr('Wybierz kierowców do obserwowania'),
                tr('Panel pokazuje kierowców zaznaczonych w LIŚCIE OT.'), target='watchlist'))
    else:
        warnings.append(tr('Brak bazy profili kierowców.'))
    if Path(dispatcher_path).is_file():
        from markus_dispatcher.data.pod_tracking import PodStore
        try:
            with closing(sqlite3.connect(Path(dispatcher_path).resolve().as_uri()+'?mode=ro', uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                tracked = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pod_followup'").fetchone()
                tracked_rows = conn.execute('SELECT job_id,sent_at FROM pod_followup').fetchall() if tracked else []
                pending_ids = {row['job_id'] for row in tracked_rows if not row['sent_at']}
                sent_ids = {row['job_id'] for row in tracked_rows if row['sent_at']}
                rows = conn.execute('SELECT j.id,j.ref_numbers,j.unload_datetime,j.pod_requirements,j.status '
                    'FROM jobs j WHERE j.status != ? ORDER BY j.id DESC', ('cancelled',)).fetchall()
            pod = PodStore(dispatcher_path, archive_root, ensure_schema=False) if tracked else None
            for rank, row in enumerate(rows):
                if cancelled():
                    raise InterruptedError('Anulowano odczyt panelu dnia.')
                if row['id'] in sent_ids:
                    continue
                state = pod.get(row['id']) if pod and row['id'] in pending_ids else None
                name = row['ref_numbers'] or f"#{row['id']}"
                if state and not state['sent_at']:
                    due = datetime.fromisoformat(state['deadline_utc']).astimezone(timezone.utc).replace(tzinfo=None) if state['deadline_utc'] else None
                    if state['needs_review']:
                        title = tr('Potwierdź termin POD po zmianie rozładunku')
                    elif state['missing_docs']:
                        title = tr('Uzupełnij dokumenty POD: ')+', '.join(state['missing_docs'])
                    else:
                        title = tr('POD gotowy — oznacz wysyłkę po jej wykonaniu')
                    if due is None or due <= cutoff or state['missing_docs'] or state['needs_review']:
                        items.append(ActionItem(f"pod-{row['id']}", 'documents', name, title,
                            row['pod_requirements'], due, job_id=row['id'], target='pod'))
                elif not state and row['pod_requirements']:
                    try:
                        unloaded = datetime.strptime(row['unload_datetime'], '%d.%m.%Y %H:%M').replace(tzinfo=WARSAW)
                    except ValueError:
                        if rank < 100 and row['status'] != 'done':
                            items.append(ActionItem(f"pod-{row['id']}", 'documents', name,
                                tr('Uzupełnij rozładunek i termin POD'), row['pod_requirements'], job_id=row['id'], target='pod'))
                        continue
                    if now-timedelta(days=30) <= unloaded <= now+timedelta(days=7):
                        items.append(ActionItem(f"pod-{row['id']}", 'documents', name, tr('Ustal wymagania i termin POD'),
                            row['pod_requirements'], job_id=row['id'], target='pod'))
        except sqlite3.Error as exc:
            warnings.append(tr('Nie odczytano zleceń: ')+str(exc))
    else:
        warnings.append(tr('Brak danych Dispatchera.'))
    items.sort(key=lambda item: (urgency(item, now)[0], item.deadline or datetime.max, item.subject.casefold()))
    return tuple(items), tuple(warnings)
