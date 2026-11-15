import re
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from wattwise.main import app
from wattwise import state


def appliance(name='Private washer'):
    return dict(name=name, price='300', energy='25', basis='100cycles', usage='4', label='A', category='Washing machine', index='')


def test_session_isolation_and_cookie(browser):
    alice, bob = browser(), browser()
    assert alice.cookies['wattwise_session'] != bob.cookies['wattwise_session']
    response = alice.post('/appliances', data=appliance())
    assert response.status_code == 200
    assert response.headers['HX-Trigger'] == 'closeEditor'
    assert response.headers['HX-Retarget'] == '#workspace'
    assert 'Private washer' in alice.get('/').text
    assert 'Private washer' not in bob.get('/').text
    alice.post('/settings', data={'rate': '.9', 'currency': 'USD', 'years': '5'})
    assert 'value="0.9"' in alice.get('/').text
    assert 'value="0.3"' in bob.get('/').text
    assert '5-year energy' in alice.get('/').text
    alice.post('/appliances/0/remove')
    assert 'Bosch Series 6' not in alice.get('/').text
    assert 'Bosch Series 6' in bob.get('/').text
    cookie = TestClient(app).get('/').headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=lax' in cookie
    assert 'max-age' not in cookie and 'expires=' not in cookie


def test_csrf_forgery_and_no_cache(browser):
    alice, bob = browser(), browser()
    assert alice.post('/reset', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert bob.post('/appliances', data=appliance(), headers={'X-CSRF-Token': alice.headers['X-CSRF-Token']}).status_code == 403
    fresh = TestClient(app)
    fresh.cookies.set('wattwise_session', 'attacker-chosen')
    response = fresh.get('/')
    assert 'attacker-chosen' not in response.headers['set-cookie']
    assert response.headers['cache-control'] == 'no-store'


def test_snapshot_sharing_copying_and_persistence(browser):
    alice, bob = browser(), browser()
    alice.post('/appliances', data=appliance('Shared washer'))
    response = alice.post('/save', data={'title': 'My shared shortlist'})
    key = re.search(r'/shared/([\w-]+)', response.text)[1]
    assert 'My shared shortlist' in bob.get('/saved').text
    assert 'Shared washer' in bob.get('/shared/' + key).text
    assert 'Use a copy in my session' in bob.get('/shared/' + key).text
    assert 'Shared washer' not in bob.get('/').text
    alice.post('/reset')
    assert 'Shared washer' in bob.get('/shared/' + key).text
    assert bob.post('/shared/' + key + '/load').headers['HX-Redirect'] == '/'
    assert 'Shared washer' in bob.get('/').text
    bob.post('/appliances/2/remove')
    assert 'Shared washer' not in bob.get('/').text
    assert 'Shared washer' in alice.get('/shared/' + key).text
    state.SESSIONS.clear()
    assert 'Shared washer' in TestClient(app).get('/shared/' + key).text


def test_expiry(browser):
    alice, bob = browser(), browser()
    alice.post('/appliances', data=appliance())
    state.SESSIONS[alice.cookies['wattwise_session']]['expires'] = 0
    response = alice.get('/')
    assert 'Private washer' not in response.text
    assert 'set-cookie' in response.headers
    assert bob.cookies['wattwise_session'] in state.SESSIONS
    assert alice.post('/reset').status_code == 403


def test_editor_validation_and_edit(browser):
    client = browser()
    assert 'hx-post="/appliances"' in client.get('/appliances/new').text
    data = appliance(); data.update(basis='hour', usage='25')
    response = client.post('/appliances', data=data)
    assert 'Check the values' in response.text
    assert 'HX-Trigger' not in response.headers
    assert 'Private washer' not in client.get('/').text
    data = appliance('Edited model'); data['index'] = '0'
    assert 'Edited model' in client.post('/appliances', data=data).text
    assert 'value="Edited model"' in client.get('/appliances/0/edit').text
    assert client.get('/appliances/99/edit').status_code == 404


def test_import_form_and_currency_warning(browser):
    client = browser()
    data = appliance(); data['query'] = 'Model 123'
    response = client.post('/lookup', data=data)
    assert 'value="Model 123"' in response.text
    assert 'value="300"' in response.text
    assert 'product-data provider' in response.text
    with patch('wattwise.main.lookup', new=AsyncMock(return_value={'specs': {'name': 'Imported', 'energy': 49, 'basis': '100cycles', 'currency': 'USD'}, 'message': 'Imported.'})):
        assert 'Source price is in USD' in client.post('/lookup', data=data).text


def test_assets_and_escaping(browser):
    client = browser()
    for name in ['htmx.min.js', 'app.js', 'style.css']:
        assert client.get('/static/' + name).status_code == 200
    client.post('/appliances', data=appliance('<script>alert(1)</script>'))
    text = client.get('/').text
    assert '<script>alert(1)</script>' not in text
    assert '&lt;script&gt;' in text
    assert 'localStorage' not in client.get('/static/app.js').text
    assert client.get('/shared/missing').status_code == 404


def test_empty_comparison_save_and_invalid_settings(browser):
    client = browser()
    client.post('/settings', data={'rate': '-1', 'currency': 'EUR', 'years': '10'})
    assert 'value="0.3"' in client.get('/').text
    client.post('/appliances/1/remove'); client.post('/appliances/0/remove')
    assert 'Add an appliance to start' in client.get('/').text
    response = client.post('/save', data={'title': '<img src=x onerror=alert(1)>'})
    assert '<img src=x' not in response.text
    assert '&lt;img' in response.text
    assert '&lt;img' in client.get('/saved').text


def test_secure_cookie_when_configured(monkeypatch):
    monkeypatch.setenv('WATTWISE_SECURE_COOKIE', '1')
    assert 'Secure' in TestClient(app, base_url='https://testserver').get('/').headers['set-cookie']
