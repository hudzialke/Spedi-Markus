"""Explain the exact computed result; no re-parsing of human-readable reasons."""
from datetime import timezone

from spedi.weekly_watch import WARSAW
from spedi.i18n import tr


def instant(value):
    return value.replace(tzinfo=timezone.utc).astimezone(WARSAW).strftime('%d.%m.%Y %H:%M') if value else '—'


def duration(minutes):
    return f'{minutes//60}:{minutes%60:02d}'


def calculation_rows(result):
    rests = list(result.rest_history)
    lookup = {identity: rest for rest in rests for identity in rest.block_ids}
    selected = {ids: (week, due) for ids, week, due in result.compensation_assignments}
    rest_rows = []
    for rest in rests:
        base = rest.duration_hours-rest.compensation_hours
        kind = 'Regularny' if base >= 45-1e-6 else ('Skrócony' if base >= 24-1e-6 else 'OD + rekompensata')
        rest_rows.append((instant(rest.start), instant(rest.end), duration(round(rest.duration_hours*60)),
                          tr(kind), duration(round(rest.compensation_hours*60)), rest.country_start or '—'))
    event_rows = []
    labels = {'created': 'Powstała rekompensata', 'paid': 'Zaliczono przy odpoczynku', 'paid_late': 'Zaliczono po terminie'}
    for event in sorted(result.compensation_events, key=lambda item: (item.at, item.kind)):
        sources = {lookup[key].block_ids: lookup[key] for key in event.source_rest_ids if key in lookup}
        source = ', '.join(instant(rest.start) for rest in sorted(sources.values(), key=lambda rest: rest.start)) or '—'
        chosen = selected.get(event.source_rest_ids)
        event_rows.append((tr(labels[event.kind]), instant(event.at), source, duration(event.minutes),
                           instant(chosen[1] if chosen else event.deadline),
                           chosen[0].strftime('%d.%m.%Y') if chosen else tr('Termin bazowy')))
    return rest_rows, event_rows


def calculation_text(name, result):
    rests, events = calculation_rows(result)
    lines = [name, 'Europe/Warsaw',
             tr('Powrót do domu / bazy: ')+instant(result.return_by),
             tr('Rozpoczęcie odpoczynku z rekompensatami: ')+instant(result.start_by),
             tr('Kolejny OT — granica 6×24 h: ')+instant(result.weekly_start_by),
             result.return_reason, result.reason, '', tr('Odpoczynki źródłowe'),
             tr('Od\tDo\tCzas\tPodstawa\tZaliczona rekompensata\tKraj startu')]
    lines.extend('\t'.join(row) for row in rests)
    lines.extend(('', tr('Historia rekompensat'), tr('Zdarzenie\tOdpoczynek od / powstanie\tŹródłowy OT od\tRekompensata\tTermin\tPrzypisany tydzień od')))
    lines.extend('\t'.join(row) for row in events)
    lines.append(tr('Wyliczenie obejmuje zapisane plany. Wiersz spłaty wskazuje początek odpoczynku, przy którym ją zaliczono.'))
    return '\n'.join(lines)+'\n'
