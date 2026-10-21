from __future__ import annotations

from datetime import datetime, timezone
import re
from urllib.parse import quote

import httpx

from markus_dispatcher.core import google_connection, settlement
from markus_dispatcher.data import settlement_store


class ConflictError(ValueError):
    def __init__(self, remote):
        super().__init__('Wiersz zmienił się w Google Sheets. Sprawdź zmiany przed aktualizacją.')
        self.remote = remote


class Sheets:
    BASE = 'https://sheets.googleapis.com/v4/spreadsheets'

    def request(self, method, path='', body=None, params=None):
        token = google_connection.access_token()
        try:
            response = httpx.request(method, self.BASE + path, json=body, params=params,
                                     headers={'Authorization': 'Bearer ' + token}, timeout=30)
        except httpx.HTTPError:
            raise ValueError('Brak odpowiedzi Google. Stan wysyłki zostanie sprawdzony przy ponowieniu.') from None
        if response.is_error:
            messages = {401: 'Zaloguj się ponownie do Google.',
                        403: 'Brak uprawnień do edycji lub niewłączone API Google. Sprawdź konfigurację i wybór pliku.',
                        404: 'Arkusz nie istnieje albo nie jest dostępny dla Dispatchera.',
                        429: 'Google ograniczył liczbę operacji. Spróbuj za chwilę.'}
            raise ValueError(messages.get(response.status_code, f'Google odrzucił operację (HTTP {response.status_code}). Sprawdź arkusz przed ponowieniem.'))
        return response.json()

    @staticmethod
    def file_id(value):
        match = re.search(r'/spreadsheets/d/([\w-]+)', value)
        value = match.group(1) if match else value.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
            raise ValueError('Nieprawidłowy identyfikator arkusza.')
        return value

    def tabs(self, file_id):
        data = self.request('GET', '/' + self.file_id(file_id), params={'fields': 'spreadsheetId,properties(title),sheets(properties)'})
        return [s['properties'] for s in data.get('sheets', [])]

    def create(self, title, tab_name):
        title, tab_name = title.strip(), tab_name.strip()
        if not title or not tab_name:
            raise ValueError('Podaj nazwę pliku i zakładki kierowcy.')
        # Entire clean layout is created in one request, without copying private data.
        body = {'properties': {'title': title, 'locale': 'pl_PL', 'timeZone': 'Europe/Warsaw'},
                'sheets': [self.template(tab_name)]}
        return self.request('POST', body=body)

    @staticmethod
    def template(title, sheet_id=0):
        def text(value):
            return {'userEnteredValue': {'stringValue': value}}
        def formula(value):
            return {'userEnteredValue': {'formulaValue': value}}
        summary = [text('Razem')] + [{} for _ in range(12)]
        for col in (5, 6, 7, 8, 10, 11):
            letter = chr(65 + col)
            summary[col] = formula(f'=SUM({letter}3:{letter}1002)')
        summary[9] = formula('=IF(H1=0,0,I1/H1)')
        rows = [{'values': summary}, {'values': [text(x) for x in settlement.HEADERS]}]
        for r in range(3, 1003):
            cells = [{} for _ in range(13)]
            cells[0] = formula(f'=IF(B{r}="","",ROW()-2)')
            cells[7] = formula(f'=IF(B{r}="","",SUM(F{r}:G{r}))')
            cells[9] = formula(f'=IF(B{r}="","",IF(H{r}=0,0,I{r}/H{r}))')
            cells[11] = formula(f'=IF(B{r}="","",I{r}-K{r})')
            rows.append({'values': cells})
        for row in [rows[0], *rows[2:]]:
            for col in (5, 6, 7, 8, 9, 10, 11):
                pattern = '0.00 "EUR"' if col in (8, 10, 11) else ('0.00 "€/km"' if col == 9 else '0.00')
                row['values'][col]['userEnteredFormat'] = {'numberFormat': {'type': 'NUMBER', 'pattern': pattern}}
            row['values'][12]['userEnteredFormat'] = {'wrapStrategy': 'WRAP'}
        header_format = {'backgroundColor': {'red': .88, 'green': .92, 'blue': .96},
                         'textFormat': {'bold': True}, 'wrapStrategy': 'WRAP'}
        for row in rows[:2]:
            for cell in row['values']:
                cell.setdefault('userEnteredFormat', {}).update(header_format)
        return {'properties': {'sheetId': sheet_id, 'title': title,
                               'gridProperties': {'rowCount': 1002, 'columnCount': 13, 'frozenRowCount': 2}},
                'data': [{'startRow': 0, 'startColumn': 0, 'rowData': rows,
                          'columnMetadata': [{'pixelSize': 140} for _ in range(13)]}]}

    def add_tab(self, file_id, title):
        tabs = self.tabs(file_id)
        if not title.strip() or any(t['title'].casefold() == title.strip().casefold() for t in tabs):
            raise ValueError('Nazwa zakładki jest pusta albo już istnieje.')
        sid = max(t['sheetId'] for t in tabs) + 1
        template = self.template(title.strip(), sid)
        # Atomic batch: an error cannot leave an uninitialized tab.
        result = self.request('POST', '/' + file_id + ':batchUpdate', {'requests': [
            {'addSheet': {'properties': template['properties']}},
            {'updateCells': {'start': {'sheetId': sid, 'rowIndex': 0, 'columnIndex': 0},
                             'rows': template['data'][0]['rowData'], 'fields': 'userEnteredValue,userEnteredFormat'}},
            {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 13},
                                           'properties': {'pixelSize': 140}, 'fields': 'pixelSize'}}]})
        return result['replies'][0]['addSheet']['properties']

    def rows(self, file_id, title, range_='A2:M1002'):
        a1 = "'" + title.replace("'", "''") + "'!" + range_
        return self.request('GET', '/' + file_id + '/values/' + quote(a1, safe=''),
                            params={'valueRenderOption': 'FORMULA', 'dateTimeRenderOption': 'FORMATTED_STRING'}).get('values', [])

    def find(self, file_id, export_id):
        matches = self.request('POST', '/' + file_id + '/developerMetadata:search', {
            'dataFilters': [{'developerMetadataLookup': {'metadataKey': 'spedi_settlement',
                                                       'metadataValue': export_id}}]}).get('matchedDeveloperMetadata', [])
        if len(matches) > 1:
            raise ValueError('Znaleziono kilka powiązań tego zlecenia. Zapis zatrzymany.')
        return matches[0]['developerMetadata'] if matches else None

    def read_bound(self, file_id, metadata, sheet_id):
        dimension = metadata.get('location', {}).get('dimensionRange', {})
        if dimension.get('sheetId') != sheet_id or dimension.get('dimension') != 'ROWS' or dimension.get('endIndex', 0) != dimension.get('startIndex', 0) + 1:
            raise ValueError('Powiązanie prowadzi do innej zakładki lub nie wskazuje jednego wiersza.')
        response = self.request('POST', '/' + file_id + '/values:batchGetByDataFilter', {
            'dataFilters': [{'developerMetadataLookup': {'metadataId': metadata['metadataId']}}],
            'valueRenderOption': 'FORMULA', 'dateTimeRenderOption': 'FORMATTED_STRING'})
        ranges = response.get('valueRanges', [])
        if len(ranges) != 1:
            raise ValueError('Nie można jednoznacznie odczytać wysłanej pozycji.')
        rows = ranges[0]['valueRange'].get('values', [])
        if len(rows) != 1:
            raise ValueError('Powiązany wiersz jest pusty lub został usunięty.')
        return settlement.inputs(rows[0])

    def publish(self, job_id, file_id, sheet_id, values, *, overwrite_remote=None):
        file_id = self.file_id(file_id)
        record = settlement_store.load(job_id)
        binding = record['binding']
        if binding and (binding['file_id'] != file_id or binding['sheet_id'] != sheet_id):
            raise ValueError('Zlecenie jest już powiązane z innym arkuszem. Nie można go przenieść przez ponowną wysyłkę.')
        tabs = self.tabs(file_id)
        target = next((t for t in tabs if t['sheetId'] == sheet_id), None)
        if not target:
            raise ValueError('Zakładka kierowcy została usunięta.')
        metadata = self.find(file_id, record['export_id'])
        if metadata:
            remote = self.read_bound(file_id, metadata, sheet_id)
            if remote != values:
                expected = overwrite_remote if overwrite_remote is not None else binding.get('last', binding.get('pending'))
                if remote != expected:
                    raise ConflictError(remote)
                # Null skips A/H/J/L; metadata lookup follows row movement between read and write.
                updated = self.request('POST', '/' + file_id + '/values:batchUpdateByDataFilter', {
                    'valueInputOption': 'RAW', 'data': [{'dataFilter': {'developerMetadataLookup': {'metadataId': metadata['metadataId']}},
                                                       'majorDimension': 'ROWS', 'values': [settlement.row_values(values)]}]})
                if updated.get('totalUpdatedRows') != 1 or updated.get('totalUpdatedSheets') != 1:
                    raise ValueError('Google nie potwierdził aktualizacji dokładnie jednej pozycji. Sprawdź powiązanie przed ponowieniem.')
        else:
            if binding.get('last') is not None:
                raise ValueError('Brak powiązania wysłanego wiersza. Nie dopisano nowej kopii.')
            rows = self.rows(file_id, target['title'])
            if not rows or len(rows[0]) < 13:
                raise ValueError('Arkusz nie ma układu rozliczenia B–M z nagłówkami w wierszu 2.')
            def normalize(s):
                return ''.join(c for c in str(s).casefold() if c.isalnum())
            for c in (1, 2, 3, 4, 5, 6, 8):
                if normalize(rows[0][c]) != normalize(settlement.HEADERS[c]):
                    raise ValueError('Nagłówki arkusza różnią się od wzorca rozliczenia. Zapis zatrzymany.')
            # Values API omits trailing empty rows. Only inspect rows which exist
            # in the sheet; computed columns may be supplied by arrays or scripts.
            last_row = min(1002, target.get('gridProperties', {}).get('rowCount', len(rows) + 1))
            def blank(value):
                return value is None or (isinstance(value, str) and not value.strip())
            candidate = next(((r, rows[r - 2] if r - 2 < len(rows) else [])
                              for r in range(3, last_row + 1)
                              if blank(settlement.inputs(rows[r - 2] if r - 2 < len(rows) else [])[0])), None)
            if not candidate:
                raise ValueError('Brak pustej komórki w kolumnie B od wiersza 3 do 1002.')
            row_number, candidate_row = candidate
            occupied = [chr(65 + col) for col, value in zip(settlement.INPUT_COLUMNS, settlement.inputs(candidate_row), strict=True)
                        if col != 1 and not blank(value)]
            if occupied:
                raise ValueError(f'Pierwsze wolne miejsce w kolumnie B to wiersz {row_number}, ale pola {", ".join(occupied)} mają już dane. '
                                 'Nie nadpisano ich i nie przeniesiono wpisu na dół arkusza. Sprawdź te pola przed wysyłką; H/J/L są ignorowane.')
            binding = dict(file_id=file_id, sheet_id=sheet_id, pending=values)
            settlement_store.save_binding(job_id, binding)
            requests = []
            for column, value in zip(settlement.INPUT_COLUMNS, values, strict=True):
                entered = {'numberValue': value} if isinstance(value, (int, float)) else {'stringValue': value}
                requests.append({'updateCells': {'start': {'sheetId': sheet_id, 'rowIndex': row_number - 1, 'columnIndex': column},
                                                  'rows': [{'values': [{'userEnteredValue': entered}]}], 'fields': 'userEnteredValue'}})
            requests.append({'createDeveloperMetadata': {'developerMetadata': {
                'metadataKey': 'spedi_settlement', 'metadataValue': record['export_id'], 'visibility': 'DOCUMENT',
                'location': {'dimensionRange': {'sheetId': sheet_id, 'dimension': 'ROWS', 'startIndex': row_number - 1, 'endIndex': row_number}}}}})
            self.request('POST', '/' + file_id + ':batchUpdate', {'requests': requests})
        binding = dict(file_id=file_id, sheet_id=sheet_id, title=target['title'], last=values,
                       sent_at=datetime.now(timezone.utc).isoformat())
        settlement_store.save_binding(job_id, binding)
        return binding
