import re

from fastapi.testclient import TestClient
import pytest
from wattwise.main import app
from wattwise import state


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv('WATTWISE_DB', str(tmp_path / 'snapshots.sqlite3'))
    state.SESSIONS.clear()
    yield
    state.SESSIONS.clear()


@pytest.fixture
def browser():
    def create():
        client = TestClient(app)
        response = client.get('/')
        assert response.status_code == 200
        csrf = re.search(r'X-CSRF-Token": "([^"]+)"', response.text)[1]
        client.headers.update({'X-CSRF-Token': csrf, 'HX-Request': 'true'})
        return client
    return create
