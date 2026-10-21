"""Desktop OAuth and Windows protected credentials. No credentials in job archives."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from markus_dispatcher import config

SCOPES = ['https://www.googleapis.com/auth/drive.file']


def _crypt(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Bezpieczne przechowywanie połączenia wymaga Windows.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Windows nie może odczytać/zabezpieczyć połączenia Google. Połącz ponownie.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def _path() -> Path:
    return config.DATA_DIR / 'google-sheets' / 'connection.dpapi'


def settings() -> dict:
    path = _path()
    return json.loads(_crypt(path.read_bytes(), decrypt=True)) if path.exists() else {}


def save_settings(value: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(_crypt(json.dumps(value).encode('utf-8')))
    temporary.replace(path)


def configure(client_file: str, api_key: str, project_number: str) -> None:
    client = json.loads(Path(client_file).read_text(encoding='utf-8-sig'))
    installed = client.get('installed', {})
    if not installed.get('client_id', '').endswith('.apps.googleusercontent.com'):
        raise ValueError('Wybierz plik klienta OAuth typu „Aplikacja komputerowa”.')
    if installed.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or installed.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('Plik OAuth ma nieprawidłowe adresy Google.')
    if not api_key.strip() or not project_number.strip().isdigit():
        raise ValueError('Podaj klucz Google Picker i numer projektu Google Cloud.')
    save_settings(dict(client=client, api_key=api_key.strip(), project_number=project_number.strip()))


def connect() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    data = settings()
    if not data.get('client'):
        raise ValueError('Najpierw skonfiguruj dostęp Google.')
    flow = InstalledAppFlow.from_client_config(data['client'], SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(
        host='127.0.0.1', port=0, open_browser=True, timeout_seconds=180,
        authorization_prompt_message='', success_message='Połączono. Możesz wrócić do Dispatchera.',
        access_type='offline', prompt='consent')
    data['credentials'] = json.loads(credentials.to_json())
    save_settings(data)


def disconnect() -> None:
    data = settings()
    data.pop('credentials', None)
    save_settings(data)


def access_token() -> str:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    data = settings()
    if not data.get('credentials'):
        raise ValueError('Kliknij „Połącz z Google” i zaloguj się w przeglądarce.')
    credentials = Credentials.from_authorized_user_info(data['credentials'], SCOPES)
    if not credentials.valid:
        credentials.refresh(Request())
        data['credentials'] = json.loads(credentials.to_json())
        save_settings(data)
    return credentials.token


def pick_spreadsheet() -> str | None:
    """Serve Picker only on an unguessable loopback route; never log its token."""
    token = access_token()
    data = settings()
    nonce = secrets.token_urlsafe(32)
    result = []
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *args):
            pass
        def do_GET(self):
            if self.path != '/' + nonce or self.headers.get('Host') != host:
                self.send_error(404)
                return
            body = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            if self.path != '/' + nonce or self.headers.get('Origin') != origin or self.headers.get('Host') != host:
                self.send_error(403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size < 2048:
                    raise ValueError()
                selected = json.loads(self.rfile.read(size))
                file_id = selected.get('id')
                if file_id is not None and (not isinstance(file_id, str) or not file_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in file_id)):
                    raise ValueError()
            except (ValueError, TypeError):
                self.send_error(400)
                return
            result.append(file_id)
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        host = f'127.0.0.1:{server.server_port}'
        origin = 'http://' + host
        options = json.dumps(dict(token=token, key=data['api_key'], appId=data['project_number']))
        html = '''<!doctype html><html lang="pl"><meta charset="utf-8"><title>Wybór arkusza — Dispatcher</title>
<link rel="preconnect" href="https://apis.google.com"><link rel="preconnect" href="https://docs.google.com">
<body><p id="status">Ładowanie wyboru arkusza Google…</p>
<script>const options = OPTIONS;
function ready(){gapi.load('picker', {callback: function(){
document.getElementById('status').textContent='Wybierz arkusz rozliczenia. Po wyborze wróć do Dispatchera.';
const view = new google.picker.DocsView(google.picker.ViewId.SPREADSHEETS);
new google.picker.PickerBuilder().addView(view).setOAuthToken(options.token)
.setDeveloperKey(options.key).setAppId(options.appId).setOrigin(location.origin)
.setCallback(async function(d){
if(d.action === google.picker.Action.PICKED || d.action === google.picker.Action.CANCEL){
await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({id:d.action===google.picker.Action.PICKED?d.docs[0].id:null})});
document.getElementById('status').textContent='Możesz wrócić do Dispatchera.';}
}).build().setVisible(true);
}, onerror:function(){document.getElementById('status').textContent='Nie udało się uruchomić wyboru Google.';}});}
</script><script async src="https://apis.google.com/js/api.js?onload=ready" onerror="document.getElementById('status').textContent='Nie udało się pobrać okna wyboru z Google. Sprawdź połączenie w przeglądarce.'"></script></body></html>'''.replace('OPTIONS', options.replace('<', '\\u003c'))
        server.timeout = 0.5
        webbrowser.open(origin + '/' + nonce)
        deadline = time.monotonic() + 180
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if not result:
        raise ValueError('Upłynął czas wyboru arkusza. Spróbuj ponownie.')
    return result[0]
