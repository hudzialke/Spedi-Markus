from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

INPUT_COLUMNS = (1, 2, 3, 4, 5, 6, 8, 10, 12)
HEADERS = ['Lp.', 'Miejsce zał.', 'Data zał.', 'Miejsce rozł.', 'Data rozł.',
           'Dolot', 'Ładowane', 'Suma km', 'Stawka', 'E/KM', 'Koszty dodatkowe', 'Suma net', 'Uwagi']
LABELS = ['Załadunek — kod i kraj', 'Data załadunku', 'Rozładunek — kod i kraj',
          'Data rozładunku', 'Dolot (km)', 'Ładowne (km)', 'Stawka (EUR)',
          'Koszty dodatkowe (EUR)', 'Uwagi']


def amount(text: str, label: str) -> float:
    try:
        value = Decimal(text.strip().replace(',', '.'))
    except (InvalidOperation, AttributeError):
        raise ValueError(f'{label}: wpisz liczbę (zero wpisz jawnie).') from None
    if not value.is_finite() or value < 0 or value > 100_000_000:
        raise ValueError(f'{label}: wymagana liczba od 0 do 100 000 000.')
    if value.as_tuple().exponent < -2:
        raise ValueError(f'{label}: najwyżej dwa miejsca po przecinku.')
    return float(value)


def payload(draft: dict, job) -> list:
    def place(key, label):
        value = draft.get(key, '').strip()
        if not value:
            raise ValueError(f'{label}: wpisz kod pocztowy i kraj.')
        return value
    def date(value):
        try:
            return datetime.strptime(value, '%d.%m.%Y %H:%M').strftime('%d.%m.%Y')
        except ValueError:
            raise ValueError('Zlecenie musi mieć poprawne daty załadunku i rozładunku.') from None
    return [place('load_place', LABELS[0]), date(job.load_datetime),
            place('unload_place', LABELS[2]), date(job.unload_datetime),
            amount(draft.get('empty_km', ''), LABELS[4]),
            amount(draft.get('loaded_km', ''), LABELS[5]),
            amount(draft.get('price', ''), LABELS[6]),
            amount(draft.get('costs', ''), LABELS[7]), draft.get('notes', '').strip()]


def row_values(values: list) -> list:
    row = [None] * 13
    for col, value in zip(INPUT_COLUMNS, values, strict=True):
        row[col] = value
    return row


def inputs(row: list) -> list:
    padded = row + [''] * max(0, 13 - len(row))
    return [padded[c] for c in INPUT_COLUMNS]


def check_formulas(row: list) -> None:
    if any(len(row) <= c or not isinstance(row[c], str) or not row[c].startswith('=')
           for c in (7, 9, 11)):
        raise ValueError('Brakuje formuł w H, J lub L. Popraw wzorzec w arkuszu; zapis zatrzymany.')


def preview(values: list, previous: list | None = None) -> str:
    lines = []
    for index, (label, value) in enumerate(zip(LABELS, values, strict=True)):
        old = previous[index] if previous and len(previous) == len(values) else None
        lines.append(f'{label}: {old} → {value}' if old is not None and old != value else f'{label}: {value}')
    return '\n'.join(lines)
