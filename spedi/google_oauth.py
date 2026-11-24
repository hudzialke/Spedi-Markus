"""Keep the desktop OAuth listener alive until the matching Google callback."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
import socket
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from spedi.i18n import tr


def authorize_desktop(client, scopes, *, cancelled=lambda: False, timeout_seconds=600):
    from google_auth_oauthlib.flow import InstalledAppFlow

    if cancelled():
        raise InterruptedError(tr('Anulowano logowanie Google.'))
    flow = InstalledAppFlow.from_client_config(client, scopes, autogenerate_code_verifier=True)
    received = threading.Event()
    lock = threading.Lock()
    result = []
    state = ''

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = False

        def server_bind(self):
            if sys.platform == 'win32' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # The callback URL carries an authorization code. Never log it.
            pass

        def reply(self, status, text):
            body = text.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            expected_host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != expected_host or len(self.path) > 16*1024:
                self.reply(400, tr('Nieprawidłowy adres powrotu z Google.'))
                return
            try:
                parsed = urlsplit(self.path)
                if parsed.path != '/' or parsed.netloc or parsed.scheme:
                    self.reply(404, tr('Nie znaleziono strony.'))
                    return
                query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
            except ValueError:
                self.reply(400, tr('Nieprawidłowa odpowiedź Google.'))
                return
            if 'code' not in query and 'error' not in query:
                self.reply(200, tr('Spedi oczekuje na zakończenie logowania Google. Wróć do karty logowania.'))
                return
            supplied = query.get('state', [])
            if len(supplied) != 1 or not secrets.compare_digest(supplied[0].encode('utf-8'), state.encode('utf-8')):
                self.reply(400, tr('Ta karta dotyczy innej próby logowania. Wróć do najnowszej karty Google.'))
                return
            code, error = query.get('code', []), query.get('error', [])
            if bool(code) == bool(error) or len(code or error) != 1 or not (code or error)[0]:
                self.reply(400, tr('Nieprawidłowa odpowiedź Google.'))
                return
            with lock:
                if not result:
                    result.append({'code': code[0]} if code else {'error': error[0]})
                    received.set()
            self.reply(200, tr('Odebrano odpowiedź Google. Wróć do Spedi, aby sprawdzić wynik połączenia. Możesz zamknąć tę kartę.'))

    try:
        server = Server(('127.0.0.1', 0), Handler)
    except OSError:
        raise RuntimeError(tr('Nie udało się uruchomić lokalnego odbioru logowania Google. Sprawdź, czy zabezpieczenia systemu nie blokują programu Spedi.')) from None
    listener = None
    try:
        flow.redirect_uri = f'http://127.0.0.1:{server.server_port}/'
        authorization_url, state = flow.authorization_url(access_type='offline', prompt='consent')
        listener = threading.Thread(target=lambda: server.serve_forever(poll_interval=.1), daemon=True)
        listener.start()
        deadline = time.monotonic()+timeout_seconds
        if not webbrowser.open(authorization_url, new=1, autoraise=True):
            raise RuntimeError(tr('Nie udało się otworzyć przeglądarki do logowania Google. Ustaw domyślną przeglądarkę i ponów połączenie.'))
        while not received.is_set():
            if cancelled():
                raise InterruptedError(tr('Anulowano logowanie Google.'))
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError(tr('Upłynął czas oczekiwania na Google. Zamknij starą kartę logowania i kliknij ponownie „Połącz / ponów logowanie”. Adres 127.0.0.1 ze starej próby już nie działa.'))
            received.wait(min(.1, remaining))
        if cancelled():
            raise InterruptedError(tr('Anulowano logowanie Google.'))
        if 'error' in result[0]:
            raise RuntimeError(tr('Google nie udzielił zgody na połączenie. Rozpocznij logowanie ponownie i zaakceptuj wymagany dostęp do plików.'))
        # Let OAuthlib verify state and PKCE during the token exchange.
        response = flow.redirect_uri.replace('http:', 'https:', 1)+'?'+urlencode({'state': state, 'code': result[0]['code']})
        flow.fetch_token(authorization_response=response, timeout=(15, 60))
        return flow.credentials
    finally:
        if listener is not None and listener.is_alive():
            server.shutdown()
            listener.join(timeout=5)
        server.server_close()
