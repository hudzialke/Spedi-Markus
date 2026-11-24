import http.client
import socket
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from spedi import google_oauth


@pytest.fixture
def flow(monkeypatch):
    from google_auth_oauthlib.flow import InstalledAppFlow
    class Flow:
        redirect_uri = None
        credentials = SimpleNamespace(token='synthetic-token')

        def authorization_url(self, **kwargs):
            assert kwargs == {'access_type': 'offline', 'prompt': 'consent'}
            assert self.redirect_uri.startswith('http://127.0.0.1:')
            return 'https://accounts.google.com/synthetic-authorization', 'synthetic-state'

        def fetch_token(self, **kwargs):
            response = urlsplit(kwargs['authorization_response'])
            assert response.scheme == 'https'
            assert parse_qs(response.query) == {'code': ['synthetic-code'], 'state': ['synthetic-state']}
            assert kwargs['timeout'] == (15, 60)
            self.exchanged = True

    instance = Flow()
    instance.exchanged = False
    def factory(client, scopes, **kwargs):
        assert client == {'installed': {}}
        assert scopes == ['https://www.googleapis.com/auth/drive.file']
        assert kwargs['autogenerate_code_verifier'] is True
        return instance
    monkeypatch.setattr(InstalledAppFlow, 'from_client_config', factory)
    return instance


def request(flow, path, *, host=None):
    address = urlsplit(flow.redirect_uri)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
    try:
        connection.request('GET', path, headers={'Host': host} if host else {})
        response = connection.getresponse()
        body = response.read()
        assert response.getheader('Cache-Control') == 'no-store'
        return response.status, body
    finally:
        connection.close()


def assert_closed(flow):
    address = urlsplit(flow.redirect_uri)
    with pytest.raises(OSError):
        socket.create_connection((address.hostname, address.port), timeout=.3)


def authorize():
    return google_oauth.authorize_desktop({'installed': {}}, ['https://www.googleapis.com/auth/drive.file'], timeout_seconds=2)


def test_oauth_ignores_browser_probes_and_idle_connections_until_matching_callback(flow, monkeypatch, capsys):
    idle = []
    def browser(url, **kwargs):
        address = urlsplit(flow.redirect_uri)
        idle.append(socket.create_connection((address.hostname, address.port), timeout=1))
        assert request(flow, '/favicon.ico')[0] == 404
        assert request(flow, '/')[0] == 200
        assert request(flow, '/?state=wrong-state&code=synthetic-code')[0] == 400
        assert request(flow, '/?state=synthetic-state&code=one&code=two')[0] == 400
        assert request(flow, '/?state=synthetic-state&code=synthetic-code', host='untrusted.example')[0] == 400
        assert request(flow, '/?state=synthetic-state&code=synthetic-code')[0] == 200
        return True
    monkeypatch.setattr(google_oauth.webbrowser, 'open', browser)
    try:
        assert authorize() is flow.credentials
        assert flow.exchanged
        assert_closed(flow)
        output = capsys.readouterr()
        assert 'synthetic-code' not in output.out+output.err
    finally:
        for connection in idle:
            connection.close()


def test_timeout_closes_listener_and_explains_stale_browser_address(flow, monkeypatch):
    monkeypatch.setattr(google_oauth.webbrowser, 'open', lambda *a, **k: True)
    with pytest.raises(TimeoutError, match='127.0.0.1'):
        google_oauth.authorize_desktop({'installed': {}}, ['https://www.googleapis.com/auth/drive.file'], timeout_seconds=.03)
    assert not flow.exchanged
    assert_closed(flow)


def test_cancellation_closes_listener_without_fetching_tokens(flow, monkeypatch):
    interrupted = []
    def browser(*args, **kwargs):
        interrupted.append(True)
        return True
    monkeypatch.setattr(google_oauth.webbrowser, 'open', browser)
    with pytest.raises(InterruptedError):
        google_oauth.authorize_desktop({'installed': {}}, ['https://www.googleapis.com/auth/drive.file'], cancelled=lambda: bool(interrupted))
    assert not flow.exchanged
    assert_closed(flow)


def test_browser_launch_failure_is_reported_and_frees_port(flow, monkeypatch):
    monkeypatch.setattr(google_oauth.webbrowser, 'open', lambda *a, **k: False)
    with pytest.raises(RuntimeError, match='przeglądarki'):
        authorize()
    assert not flow.exchanged
    assert_closed(flow)


def test_google_denied_consent_is_not_saved_as_success(flow, monkeypatch):
    def browser(*args, **kwargs):
        assert request(flow, '/?'+urlencode({'state': 'synthetic-state', 'error': 'access_denied'}))[0] == 200
        return True
    monkeypatch.setattr(google_oauth.webbrowser, 'open', browser)
    with pytest.raises(RuntimeError, match='zgody'):
        authorize()
    assert not flow.exchanged
    assert_closed(flow)


def test_token_exchange_failure_still_closes_listener(flow, monkeypatch):
    def browser(*args, **kwargs):
        request(flow, '/?state=synthetic-state&code=synthetic-code')
        return True
    def fail(**kwargs):
        raise RuntimeError('synthetic token exchange failure')
    monkeypatch.setattr(google_oauth.webbrowser, 'open', browser)
    monkeypatch.setattr(flow, 'fetch_token', fail)
    with pytest.raises(RuntimeError, match='synthetic token exchange failure'):
        authorize()
    assert_closed(flow)


def test_failed_sign_in_preserves_existing_credentials(monkeypatch):
    from markus_dispatcher.core import google_connection
    original = {'client': {'installed': {}}, 'credentials': {'refresh_token': 'synthetic-old'}}
    saved = []
    monkeypatch.setattr(google_connection, 'settings', lambda: original)
    monkeypatch.setattr(google_connection, 'save_settings', saved.append)
    def fail(*args, **kwargs):
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(google_oauth, 'authorize_desktop', fail)
    with pytest.raises(TimeoutError):
        google_connection.connect()
    assert not saved
    assert original['credentials']['refresh_token'] == 'synthetic-old'
