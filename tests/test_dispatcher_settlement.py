from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict
from urllib.parse import unquote

import pytest

from markus_dispatcher.core import settlement, google_connection
from markus_dispatcher.core.google_sheets import Sheets, ConflictError
from markus_dispatcher.core.models import JobData
from markus_dispatcher.data import db, settlement_store


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(db.config, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(db.config, 'ARCHIVE_ROOT', tmp_path / 'archive')
    monkeypatch.setattr(db.config, 'ROZPISKA_DB_CANDIDATE', tmp_path / 'missing.db')
    db.init_db()
    driver = db.add_driver('Kierowca testowy')
    return db.save_job(JobData(driver_id=driver.id, load_datetime='11.09.2026 10:00', unload_datetime='12.09.2026 14:00'))


@pytest.fixture
def values(job):
    return settlement.payload(dict(load_place='00-001 PL', unload_place='10115 DE',
                                   empty_km='25,5', loaded_km='600', price='900', costs='15', notes='Parking'), job)


class MemorySheets(Sheets):
    """Small wire-level fake: metadata points at a row, not an A1 address."""
    def __init__(self):
        self.calls = []
        self.metadata = []
        self.grid = [settlement.HEADERS.copy()]
        for r in range(3, 7):
            self.grid.append(['', '', '', '', '', '', '', f'=SUM(F{r}:G{r})', '', f'=I{r}/H{r}', '', f'=I{r}-K{r}', ''])
        self.timeout_after_commit = False
        self.tab_title = 'Test'

    def request(self, method, path='', body=None, params=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == 'GET' and '/values/' not in path:
            return {'sheets': [{'properties': {'sheetId': 7, 'title': self.tab_title}}]}
        if path.endswith('/developerMetadata:search'):
            identity = body['dataFilters'][0]['developerMetadataLookup']['metadataValue']
            return {'matchedDeveloperMetadata': [{'developerMetadata': m} for m in self.metadata if m['metadataValue'] == identity]}
        if '/values/' in path:
            assert "'" + self.tab_title.replace("'", "''") + "'!A2:M1002" in unquote(path)
            return {'values': copy.deepcopy(self.grid)}
        if path.endswith('/values:batchGetByDataFilter'):
            mid = body['dataFilters'][0]['developerMetadataLookup']['metadataId']
            meta = next(m for m in self.metadata if m['metadataId'] == mid)
            row = meta['location']['dimensionRange']['startIndex'] - 1
            return {'valueRanges': [{'valueRange': {'values': [self.grid[row].copy()]}}]}
        if path.endswith('/values:batchUpdateByDataFilter'):
            mid = body['data'][0]['dataFilter']['developerMetadataLookup']['metadataId']
            meta = next(m for m in self.metadata if m['metadataId'] == mid)
            row = meta['location']['dimensionRange']['startIndex'] - 1
            for col, value in enumerate(body['data'][0]['values'][0]):
                if value is not None:
                    self.grid[row][col] = value
            return {'totalUpdatedRows': 1, 'totalUpdatedSheets': 1}
        if path.endswith(':batchUpdate'):
            for request in body['requests']:
                if 'updateCells' in request:
                    req = request['updateCells']
                    value = req['rows'][0]['values'][0]['userEnteredValue']
                    self.grid[req['start']['rowIndex'] - 1][req['start']['columnIndex']] = next(iter(value.values()))
                else:
                    meta = copy.deepcopy(request['createDeveloperMetadata']['developerMetadata'])
                    meta['metadataId'] = len(self.metadata) + 1
                    self.metadata.append(meta)
            if self.timeout_after_commit:
                self.timeout_after_commit = False
                raise ValueError('Timeout after remote commit')
            return {}
        raise AssertionError((method, path))


def test_payload_and_totals_are_separate(job, values):
    assert values == ['00-001 PL', '11.09.2026', '10115 DE', '12.09.2026', 25.5, 600., 900., 15., 'Parking']
    row = settlement.row_values(values)
    assert [row[c] for c in (0, 7, 9, 11)] == [None] * 4


@pytest.mark.parametrize('bad', ['', '-1', 'NaN', 'Infinity', 'abc', '1.001', '100000001'])
def test_invalid_money_and_missing_zero(bad):
    with pytest.raises(ValueError):
        settlement.amount(bad, 'Stawka')


def test_private_store_not_in_job_or_archive(job):
    secret = {'price': '1234.56', 'notes': 'PRIVATE_TEST_VALUE'}
    settlement_store.save_draft(job.id, secret)
    identity = settlement_store.load(job.id)['export_id']
    assert settlement_store.load(job.id)['export_id'] == identity
    assert settlement_store.load(job.id)['draft'] == secret
    assert 'price' not in asdict(db.get_job(job.id))
    archived = db.sync_job_to_archive(job, instruction_footer='')
    for path in (db.config.ARCHIVE_ROOT / archived.archive_path).rglob('*'):
        if path.is_file():
            assert b'PRIVATE_TEST_VALUE' not in path.read_bytes()


def test_send_update_deduplicate_preserve_formulas(job, values):
    api = MemorySheets()
    before = [api.grid[1][c] for c in (7, 9, 11)]
    api.publish(job.id, 'file1', 7, values)
    api.publish(job.id, 'file1', 7, values)
    changed = values.copy()
    changed[6] = 950.
    api.publish(job.id, 'file1', 7, changed)
    assert len(api.metadata) == 1
    assert settlement.inputs(api.grid[1]) == changed
    assert [api.grid[1][c] for c in (7, 9, 11)] == before
    update = [b for _, p, b in api.calls if p.endswith('/values:batchUpdateByDataFilter')]
    assert len(update) == 1 and update[0]['valueInputOption'] == 'RAW'


def test_retry_after_uncertain_commit_does_not_duplicate(job, values):
    api = MemorySheets()
    api.timeout_after_commit = True
    with pytest.raises(ValueError, match='Timeout'):
        api.publish(job.id, 'file1', 7, values)
    assert 'pending' in settlement_store.load(job.id)['binding']
    api.publish(job.id, 'file1', 7, values)
    assert len(api.metadata) == 1
    assert settlement_store.load(job.id)['binding']['last'] == values


def test_shifted_row_and_renamed_tab_are_found_by_metadata(job, values):
    api = MemorySheets()
    api.publish(job.id, 'file1', 7, values)
    api.grid.insert(1, ['other'] * 13)
    location = api.metadata[0]['location']['dimensionRange']
    location['startIndex'] += 1
    location['endIndex'] += 1
    api.tab_title = "Nowa ' nazwa"
    changed = values.copy()
    changed[-1] = 'Poprawione'
    api.publish(job.id, 'file1', 7, changed)
    assert settlement.inputs(api.grid[2]) == changed
    assert api.grid[1] == ['other'] * 13


def test_conflict_requires_exact_remote_confirmation(job, values):
    api = MemorySheets()
    api.publish(job.id, 'file1', 7, values)
    api.grid[1][8] = 1100.
    with pytest.raises(ConflictError) as exc:
        api.publish(job.id, 'file1', 7, values)
    remote = exc.value.remote
    api.grid[1][8] = 1200.
    with pytest.raises(ConflictError):
        api.publish(job.id, 'file1', 7, values, overwrite_remote=remote)
    api.publish(job.id, 'file1', 7, values, overwrite_remote=settlement.inputs(api.grid[1]))
    assert api.grid[1][8] == 900.


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'other_slot'])
def test_unsafe_update_is_refused(job, values, mutation):
    api = MemorySheets()
    api.publish(job.id, 'file1', 7, values)
    if mutation == 'missing':
        api.metadata.clear()
    elif mutation == 'duplicate':
        api.metadata.append(copy.deepcopy(api.metadata[0]))
    elif mutation == 'other_slot':
        api.metadata[0]['location']['dimensionRange']['sheetId'] = 8
    before = copy.deepcopy(api.grid)
    with pytest.raises(ValueError):
        api.publish(job.id, 'file1', 7, values)
    assert api.grid == before


@pytest.mark.parametrize('computed', [['', '', ''], [0, 0, 0], ['=ARRAYFORMULA(F9:F+G9:G)', '=CUSTOM_RATE()', '=CUSTOM_NET()']])
def test_first_free_row9_and_update_leave_computed_cells_untouched(job, values, computed):
    api = MemorySheets()
    api.grid = [settlement.HEADERS.copy()] + [['manual'] * 13 for _ in range(6)] + [[''] * 13]
    for col, value in zip((7, 9, 11), computed):
        api.grid[7][col] = value
    original = copy.deepcopy(api.grid)
    api.publish(job.id, 'file1', 7, values)
    assert api.metadata[0]['location']['dimensionRange']['startIndex'] == 8
    assert api.grid[:7] == original[:7]
    assert [api.grid[7][c] for c in (7, 9, 11)] == computed
    changed = values.copy()
    changed[6] = 1234.
    api.publish(job.id, 'file1', 7, changed)
    assert settlement.inputs(api.grid[7]) == changed
    assert [api.grid[7][c] for c in (7, 9, 11)] == computed
    request = next(b for _, p, b in api.calls if p.endswith(':batchUpdate'))
    assert {r['updateCells']['start']['columnIndex'] for r in request['requests'] if 'updateCells' in r} == set(settlement.INPUT_COLUMNS)


def test_trailing_empty_row_omitted_by_google_is_usable(job, values):
    class Trimmed(MemorySheets):
        def tabs(self, file_id):
            return [{'sheetId': 7, 'title': 'Test', 'gridProperties': {'rowCount': 9}}]
        def rows(self, *args, **kwargs):
            return copy.deepcopy(self.grid[:7])
    api = Trimmed()
    api.grid = [settlement.HEADERS.copy()] + [['manual'] * 13 for _ in range(6)] + [[''] * 13]
    api.publish(job.id, 'file1', 7, values)
    assert settlement.inputs(api.grid[7]) == values


@pytest.mark.parametrize('column,value', [(2, '01.01.2026'), (5, 0), (8, 100), (12, 'Uwaga')])
def test_first_empty_b_with_other_input_stops_instead_of_skipping_to_bottom(job, values, column, value):
    api = MemorySheets()
    api.grid[1][column] = value
    before = copy.deepcopy(api.grid)
    with pytest.raises(ValueError, match='wiersz 3'):
        api.publish(job.id, 'file1', 7, values)
    assert api.grid == before
    assert not api.metadata


def test_first_empty_b_ignores_computed_columns_even_with_later_empty_rows(job, values):
    api = MemorySheets()
    api.grid[1][1] = ' \u00a0 '
    api.grid[1][7], api.grid[1][9], api.grid[1][11] = 500, '=CUSTOM()', '#DIV/0!'
    before = copy.deepcopy(api.grid)
    api.publish(job.id, 'file1', 7, values)
    assert api.metadata[0]['location']['dimensionRange']['startIndex'] == 2
    assert [api.grid[1][c] for c in (7, 9, 11)] == [before[1][c] for c in (7, 9, 11)]
    assert api.grid[2:] == before[2:]


def test_different_target_refused(job, values):
    api = MemorySheets()
    api.publish(job.id, 'file1', 7, values)
    with pytest.raises(ValueError, match='innym arkuszem'):
        api.publish(job.id, 'file2', 7, values)


def test_occupied_manual_rows_not_overwritten(job, values):
    api = MemorySheets()
    api.grid[1][1] = 'MANUAL'
    api.publish(job.id, 'file1', 7, values)
    assert api.grid[1][1] == 'MANUAL'
    assert settlement.inputs(api.grid[2]) == values


def test_wrong_headers_refused(job, values):
    api = MemorySheets()
    api.grid[0][8] = 'INNE DANE'
    with pytest.raises(ValueError, match='Nagłówki'):
        api.publish(job.id, 'file1', 7, values)
    assert not api.metadata


def test_formula_injection_stays_literal(job, values):
    api = MemorySheets()
    values[-1] = '=IMPORTXML("https://example.invalid", "//x")'
    api.publish(job.id, 'file1', 7, values)
    request = next(b for _, p, b in api.calls if p.endswith(':batchUpdate'))
    note_cell = request['requests'][-2]['updateCells']['rows'][0]['values'][0]['userEnteredValue']
    assert note_cell == {'stringValue': values[-1]}


def test_new_template_has_only_clean_inputs_and_correct_formulas():
    template = Sheets.template('Test')
    rows = template['data'][0]['rowData']
    assert len(rows) == 1002
    for r, row in enumerate(rows[2:], 3):
        cells = row['values']
        assert all('userEnteredValue' not in cells[c] for c in settlement.INPUT_COLUMNS)
        assert cells[7]['userEnteredValue']['formulaValue'] == f'=IF(B{r}="","",SUM(F{r}:G{r}))'
        assert f'I{r}-K{r}' in cells[11]['userEnteredValue']['formulaValue']
    assert rows[0]['values'][8]['userEnteredValue']['formulaValue'] == '=SUM(I3:I1002)'


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_connection_is_encrypted_and_disconnect_removes_token(job):
    data = {'credentials': {'token': 'TEST_ONLY_SECRET_TOKEN'}, 'project_number': '123'}
    google_connection.save_settings(data)
    content = google_connection._path().read_bytes()
    assert b'TEST_ONLY_SECRET_TOKEN' not in content
    assert google_connection.settings() == data
    google_connection.disconnect()
    assert 'credentials' not in google_connection.settings()


@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_desktop_oauth_configuration_is_saved_without_original_file(job, tmp_path):
    client_file = tmp_path / 'oauth-client.json'
    client_file.write_text(json.dumps({'installed': {
        'client_id': 'synthetic-client.apps.googleusercontent.com',
        'client_secret': 'TEST_ONLY_CLIENT_SECRET',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }}), encoding='utf-8')

    google_connection.configure(str(client_file), 'TEST_ONLY_PICKER_KEY', '123456789')
    saved = google_connection._path().read_bytes()
    assert b'TEST_ONLY_CLIENT_SECRET' not in saved
    assert b'TEST_ONLY_PICKER_KEY' not in saved
    assert google_connection.settings()['project_number'] == '123456789'
    client_file.unlink()
    assert google_connection.settings()['client']['installed']['client_id'] == 'synthetic-client.apps.googleusercontent.com'


def test_ui_save_restore_and_no_leak_to_instruction(job, monkeypatch, tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from markus_dispatcher.ui.job_pane import JobPane
    from markus_dispatcher.core.instruction import build_instruction
    app = QApplication.instance() or QApplication([])
    pane = JobPane()
    pane.load_job(job)
    pane.settlement.fields['price'].setText('123.45')
    pane.settlement.fields['notes'].setText('PRIVATE_UI_TEST')
    pane._save(show_message=False)
    pane.new_job(driver_id=job.driver_id)
    assert pane.settlement.fields['price'].text() == ''
    pane.load_job(db.get_job(job.id))
    assert pane.settlement.fields['price'].text() == '123.45'
    assert 'PRIVATE_UI_TEST' not in build_instruction(pane.collect())
    pane.resize(1280, 1000)
    pane.show()
    app.processEvents()
    assert pane.settlement.publish_btn.text() == 'Wyślij do arkusza'
    pane.close()


def test_worker_returns_result_and_exception(job):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QWidget
    from markus_dispatcher.ui.settlement_panel import run_task
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    assert run_task(parent, 'Test', lambda: 42) == 42
    def fail():
        raise ValueError('test error')
    with pytest.raises(ValueError, match='test error'):
        run_task(parent, 'Test', fail)


def test_create_is_single_request_without_private_data():
    class Capture(Sheets):
        def request(self, method, path='', body=None, params=None):
            assert method == 'POST' and path == ''
            assert body['properties']['title'] == 'Rozliczenie testowe'
            assert body['properties']['locale'] == 'pl_PL'
            assert body['sheets'][0]['properties']['title'] == 'Test'
            assert 'spedi_settlement' not in json.dumps(body)
            return {'spreadsheetId': 'new_file'}
    assert Capture().create('Rozliczenie testowe', 'Test')['spreadsheetId'] == 'new_file'


def test_add_tab_atomic_and_duplicate_refused():
    class Capture(Sheets):
        def tabs(self, file_id):
            return [{'sheetId': 4, 'title': 'Existing'}]
        def request(self, method, path='', body=None, params=None):
            assert path == '/file:batchUpdate'
            requests = body['requests']
            assert len(requests) == 3
            assert requests[0]['addSheet']['properties']['sheetId'] == 5
            assert len(requests[1]['updateCells']['rows']) == 1002
            return {'replies': [{'addSheet': {'properties': {'sheetId': 5, 'title': 'New'}}}]}
    api = Capture()
    assert api.add_tab('file', 'New')['sheetId'] == 5
    with pytest.raises(ValueError, match='już istnieje'):
        api.add_tab('file', 'EXISTING')


def test_zero_updated_rows_is_not_saved_as_success(job, values):
    class Gone(MemorySheets):
        def request(self, method, path='', body=None, params=None):
            if path.endswith('/values:batchUpdateByDataFilter'):
                return {'totalUpdatedRows': 0, 'totalUpdatedSheets': 0}
            return super().request(method, path, body, params)
    api = Gone()
    api.publish(job.id, 'file1', 7, values)
    changed = values.copy()
    changed[-1] = 'new'
    with pytest.raises(ValueError, match='nie potwierdził'):
        api.publish(job.id, 'file1', 7, changed)
    assert settlement_store.load(job.id)['binding']['last'] == values


def test_oauth_uses_browser_pkce_and_file_scope(job, monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow
    google_connection.save_settings({'client': {'installed': {}}, 'project_number': '123'})
    class Credentials:
        def to_json(self):
            return '{"refresh_token":"TEST_ONLY"}'
    class Flow:
        def run_local_server(self, **kwargs):
            assert kwargs['host'] == '127.0.0.1' and kwargs['port'] == 0
            assert kwargs['timeout_seconds'] == 180 and kwargs['open_browser']
            return Credentials()
    def factory(client, scopes, **kwargs):
        assert scopes == ['https://www.googleapis.com/auth/drive.file']
        assert kwargs['autogenerate_code_verifier'] is True
        return Flow()
    monkeypatch.setattr(InstalledAppFlow, 'from_client_config', factory)
    google_connection.connect()
    assert google_connection.settings()['credentials']['refresh_token'] == 'TEST_ONLY'


def test_two_jobs_keep_separate_bindings(job, values):
    second = db.save_job(JobData(driver_id=job.driver_id))
    api = MemorySheets()
    api.publish(job.id, 'file1', 7, values)
    other = values.copy()
    other[-1] = 'Drugi fracht'
    api.publish(second.id, 'file1', 7, other)
    assert len(api.metadata) == 2
    assert settlement_store.load(job.id)['export_id'] != settlement_store.load(second.id)['export_id']
    assert settlement.inputs(api.grid[1]) == values
    assert settlement.inputs(api.grid[2]) == other


def test_picker_idle_browser_connection_does_not_block_page(job, monkeypatch):
    import http.client
    import socket
    import threading
    import time
    from urllib.parse import urlsplit

    monkeypatch.setattr(google_connection, 'access_token', lambda: 'FAKE_TEST_TOKEN')
    monkeypatch.setattr(google_connection, 'settings', lambda: {'api_key': 'TEST_KEY', 'project_number': '123'})
    errors, durations, threads = [], [], []

    def open_browser(url):
        def browse():
            target = urlsplit(url)
            origin = f'http://{target.netloc}'
            try:
                # Chromium browsers may reserve a socket without sending a request.
                with socket.create_connection((target.hostname, target.port), timeout=2) as idle:
                    start = time.monotonic()
                    conn = http.client.HTTPConnection(target.hostname, target.port, timeout=2)
                    try:
                        conn.request('GET', target.path)
                        response = conn.getresponse()
                        body = response.read()
                        assert response.status == 200
                        assert response.getheader('Cache-Control') == 'no-store'
                        assert int(response.getheader('Content-Length')) == len(body)
                        durations.append(time.monotonic() - start)
                        conn.request('POST', target.path, json.dumps({'id': 'selected_test_file'}),
                                     {'Origin': origin, 'Content-Type': 'application/json'})
                        response = conn.getresponse()
                        assert response.status == 200
                        response.read()
                    finally:
                        conn.close()
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=browse, daemon=True)
        threads.append(thread)
        thread.start()
        return True

    monkeypatch.setattr(google_connection.webbrowser, 'open', open_browser)
    # Bound the outer picker loop if a regression prevents the POST callback.
    real_clock = time.monotonic
    start = real_clock()
    monkeypatch.setattr(google_connection.time, 'monotonic', lambda: real_clock() + (181 if real_clock() - start > 4 else 0))
    assert google_connection.pick_spreadsheet() == 'selected_test_file'
    for thread in threads:
        thread.join(timeout=3)
    assert not errors
    assert durations and durations[0] < 2
