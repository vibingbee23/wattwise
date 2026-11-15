from fastapi.testclient import TestClient
import pytest
from wattwise.main import app

client = TestClient(app)


def appliance(**overrides):
    return dict(name='Washer', price=600, energy=50, basis='100cycles', usage=4) | overrides


@pytest.mark.parametrize('basis,energy,usage,annual', [
    ('100cycles', 50, 4, 104), ('cycle', .5, 4, 104),
    ('hour', 1.5, 8, 4380), ('year', 180, 0, 180),
])
@pytest.mark.parametrize('years', [5, 10])
def test_energy_units_and_horizons(basis, energy, usage, annual, years):
    response = client.post('/api/compare', json={
        'appliances': [appliance(basis=basis, energy=energy, usage=usage)], 'rate': .3, 'years': years,
    })
    assert response.status_code == 200
    result = response.json()['results'][0]
    assert result['annual_kwh'] == pytest.approx(annual)
    assert result['five_kwh'] == pytest.approx(annual * 5)
    assert result['ten_kwh'] == pytest.approx(annual * 10)
    assert result['five_total'] == pytest.approx(600 + annual * .3 * 5)
    assert result['ten_total'] == pytest.approx(600 + annual * .3 * 10)
    assert result['total'] == pytest.approx(600 + annual * .3 * years)


@pytest.mark.parametrize('override', [dict(price=-1), dict(energy=-1), dict(usage=-1), dict(basis='hour', usage=25), dict(name='  '), dict(basis='invalid')])
def test_invalid_appliances(override):
    assert client.post('/api/compare', json={'appliances': [appliance(**override)]}).status_code == 422


def test_zero_rate_and_limits():
    result = client.post('/api/compare', json={'appliances': [appliance()], 'rate': 0}).json()['results'][0]
    assert result['total'] == 600
    assert client.post('/api/compare', json={'appliances': [appliance()] * 13}).status_code == 422
    assert client.post('/api/compare', json={'rate': -1}).status_code == 422
    assert client.post('/api/compare', json={'years': 7}).status_code == 422
    assert client.post('/api/compare', json={'appliances': []}).json() == {'results': []}
