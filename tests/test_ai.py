import asyncio
import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import httpx
import pytest

from wattwise import ai, state
from wattwise.main import app
from wattwise.importer import ProductPage, lookup
from wattwise.scraping import Extraction


def findings(match='exact', url='https://example.com/owner-reviews'):
    return ai.Research(model_match=match, **{key: {'text': f'{label} evidence', 'source_urls': [url]} for key, label in ai.FINDING_LABELS.items()})


def outputs(url='https://example.com/owner-reviews'):
    return [{'type': 'web_search_call', 'action': {'sources': [{'url': url, 'title': 'Owner review source'}]}}]


def suggestions(**overrides):
    return ai.SpecSuggestions(**(dict(name=None, price=None, currency=None, label=None, measurement=None) | overrides))


def test_admin_auth_and_no_key_disclosure(browser, monkeypatch):
    client = browser()
    assert client.get('/admin').status_code == 404
    monkeypatch.setenv('WATTWISE_ADMIN_TOKEN', 'admin-test-token')
    assert client.get('/admin').status_code == 401
    assert client.post('/admin/ai', data={'api_key': 'private-value', 'model': 'gpt-4.1-mini'}).status_code == 401
    client.auth = ('admin', 'wrong-token')
    assert client.get('/admin').status_code == 401
    client.auth = ('admin', 'admin-test-token')
    assert client.get('/admin').status_code == 200
    response = client.post('/admin/ai', data={'api_key': 'private-value', 'model': 'gpt-4.1-mini'})
    assert response.status_code == 200
    assert ai.settings().enabled
    assert 'private-value' not in response.text
    assert 'private-value' not in client.get('/admin').text
    assert 'private-value' not in client.get('/').text
    assert 'private-value' not in repr(ai.settings())
    other = browser()
    assert other.get('/admin').status_code == 401
    assert other.post('/admin/ai', data={'disable': 'yes', 'model': 'gpt-4.1-mini'}).status_code == 401
    assert client.post('/admin/ai', data={'disable': 'yes', 'model': 'gpt-4.1-mini'}).status_code == 200
    assert not ai.settings().enabled


def test_environment_config_and_atomic_update(monkeypatch):
    monkeypatch.setenv('WATTWISE_LLM_API_KEY', 'server-env-key')
    assert ai.settings().enabled
    with pytest.raises(ValueError):
        ai.configure(api_key='new-value', model='bad model')
    assert ai.settings().api_key == 'server-env-key'
    ai.configure(disable=True)
    assert not ai.settings().enabled


def test_fallback_only_fills_missing_fields_and_provider_error_degrades():
    ai.configure(api_key='test')
    page = ProductPage('https://example.com/product', Extraction(specs={'name': 'Washer', 'price': 600}, evidence={'price': 'Table'}), '49 kWh per 100 cycles')
    with patch('wattwise.importer._fetch_product', new=AsyncMock(return_value=page)), patch('wattwise.ai.fill_specs', new=AsyncMock(return_value=({'price': 1, 'energy': 49, 'basis': '100cycles'}, {'energy': 'AI-assisted', 'basis': 'AI-assisted'}))):
        result = asyncio.run(lookup('https://example.com/product'))
    assert result['specs']['price'] == 600
    assert result['specs']['energy'] == 49
    assert result['evidence']['energy'] == 'AI-assisted'
    page.extraction = Extraction(specs={'name': 'Washer'})
    with patch('wattwise.importer._fetch_product', new=AsyncMock(return_value=page)), patch('wattwise.ai.fill_specs', new=AsyncMock(side_effect=ai.AIUnavailable('AI unavailable'))):
        result = asyncio.run(lookup('https://example.com/product'))
    assert result['specs'] == {'name': 'Washer'}
    assert result['warnings'] == ['AI unavailable']


def test_classic_conflicts_do_not_trigger_ai_guessing():
    ai.configure(api_key='test')
    page = ProductPage('https://example.com', Extraction(warnings=['Conflicting energy measurements']), 'Ambiguous text')
    fill = AsyncMock()
    with patch('wattwise.importer._fetch_product', new=AsyncMock(return_value=page)), patch('wattwise.ai.fill_specs', new=fill):
        asyncio.run(lookup('https://example.com'))
    fill.assert_not_awaited()


def test_suggestions_need_verbatim_evidence_and_matching_values():
    ai.configure(api_key='test')
    response = suggestions(name={'value': 'Model X', 'quote': 'Model X'}, price={'value': 50, 'quote': 'Costs 600 EUR'}, measurement={'energy': 49, 'basis': '100cycles', 'quote': '49 kWh per 100 cycles'})
    with patch('wattwise.ai.response_json', new=AsyncMock(return_value=(response, []))):
        specs, evidence = asyncio.run(ai.fill_specs('Model X. Costs 600 EUR. 49 kWh per 100 cycles', {'name', 'price', 'energy', 'basis'}))
    assert specs == {'name': 'Model X', 'energy': 49, 'basis': '100cycles'}
    assert all('AI-assisted' in value for value in evidence.values())
    with patch('wattwise.ai.response_json', new=AsyncMock(return_value=(response, []))):
        specs, _ = asyncio.run(ai.fill_specs('No evidence here', {'name', 'price', 'energy', 'basis'}))
    assert specs == {}


def test_sourced_research_cache_and_market():
    ai.configure(api_key='test')
    provider = AsyncMock(return_value=(findings('series'), outputs()))
    async def run():
        with patch('wattwise.ai.response_json', new=provider):
            first = await ai.research('Model X', 'Washer', 'Netherlands')
            again = await ai.research('Model X', 'Washer', 'Netherlands')
            await ai.research('Model X', 'Washer', 'Germany')
        return first, again
    first, again = asyncio.run(run())
    assert first == again
    assert provider.await_count == 2
    assert first['model_match'] == 'series'
    assert len(first['findings']) == 6
    assert first['sources'] == {'https://example.com/owner-reviews': 'Owner review source'}
    assert first['market'] == 'Netherlands'
    assert provider.call_args.kwargs['search'] is True


@pytest.mark.parametrize('match,provided_outputs', [('exact', []), ('insufficient', outputs())])
def test_unsupported_research_claims_are_replaced(match, provided_outputs):
    ai.configure(api_key='test')
    with patch('wattwise.ai.response_json', new=AsyncMock(return_value=(findings(match), provided_outputs))):
        report = asyncio.run(ai.research('Model X', 'Washer'))
    assert not report['sources']
    assert all(finding['text'].startswith('Not enough sourced') for finding in report['findings'])


def test_provider_errors_never_leak_key():
    config = ai.Settings('private-secret')
    original = httpx.AsyncClient
    def respond(request):
        assert request.headers['authorization'] == 'Bearer private-secret'
        payload = json.loads(request.content)
        assert payload['store'] is False
        assert payload['tools'] == [{'type': 'web_search'}]
        return httpx.Response(401, json={'error': {'message': 'Rejected private-secret'}})
    with patch('wattwise.ai.httpx.AsyncClient', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)):
        with pytest.raises(ai.AIUnavailable) as error:
            asyncio.run(ai.response_json(config, ai.Research, 'instructions', 'input', search=True))
    assert 'private-secret' not in str(error.value)


def test_research_does_not_modify_facts_or_winner(browser):
    ai.configure(api_key='test')
    client = browser()
    session = state.SESSIONS[client.cookies['wattwise_session']]
    before = json.dumps(session['comparison'], sort_keys=True)
    before_html = client.get('/').text
    assert 'hx-get="/appliances/0/research"' in before_html
    with patch('wattwise.ai.response_json', new=AsyncMock(return_value=(findings(), outputs()))):
        response = client.get('/appliances/0/research')
    assert response.status_code == 200
    assert 'Common flaws evidence' in response.text
    assert 'do not affect the best-value calculation' in response.text
    assert json.dumps(session['comparison'], sort_keys=True) == before
    assert client.get('/').text == before_html
    saved = client.post('/save', data={'title': 'Shared with research'})
    import re
    key = re.search(r'/shared/([\w-]+)', saved.text)[1]
    other = browser()
    assert f'/shared/{key}/appliances/0/research' in other.get('/shared/' + key).text
    assert 'private-secret' not in saved.text


def test_research_unavailable_and_local_market_isolated(browser):
    alice, bob = browser(), browser()
    assert 'disabled' in alice.get('/appliances/0/research').text
    alice.post('/settings', data={'rate': '.3', 'currency': 'EUR', 'years': '10', 'market': 'Netherlands'})
    assert 'value="Netherlands"' in alice.get('/').text
    assert 'value="Netherlands"' not in bob.get('/').text
    ai.configure(api_key='test')
    with patch('wattwise.ai.research', new=AsyncMock(side_effect=ai.AIUnavailable('AI unavailable'))):
        assert 'Costs and manual specs remain available' in alice.get('/appliances/0/research').text


def test_search_then_structured_summary_contract():
    original = httpx.AsyncClient
    requests = []
    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if 'tools' in payload:
            assert 'text' not in payload
            return httpx.Response(200, json={'status': 'completed', 'output': outputs() + [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Exact model: a recurring quirk is documented.'}]}]})
        assert payload['text']['format']['type'] == 'json_schema'
        assert 'allowed_sources' in payload['input']
        return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': findings().model_dump_json()}]}]})
    with patch('wattwise.ai.httpx.AsyncClient', side_effect=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)):
        document, evidence = asyncio.run(ai.response_json(ai.Settings('test'), ai.Research, 'Research using web search', 'Model X', search=True))
    assert len(requests) == 2
    assert document.model_match == 'exact'
    assert ai.search_sources(evidence) == {'https://example.com/owner-reviews': 'Owner review source'}
