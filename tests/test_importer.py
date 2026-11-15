import asyncio
import socket
from unittest.mock import patch, AsyncMock

import httpx
import pytest
from wattwise.importer import extract_specs, lookup, public_address


def test_product_extraction():
    html = '''<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Example Washer","offers":{"price":"549.99","priceCurrency":"EUR"}}]}</script><p>49 kWh / 100 cycles. Energy efficiency class: A</p>'''
    assert extract_specs(html) == dict(name='Example Washer', price=549.99, currency='EUR', energy=49, basis='100cycles', label='A')
    assert extract_specs('<p>Nothing here</p>') == {}
    assert 'price' not in extract_specs('<script type="application/ld+json">{"@type":"Product","offers":{"price":"NaN"}}</script>')


@pytest.mark.parametrize('url', ['http://127.0.0.1', 'http://169.254.169.254', 'http://[::1]', 'file:///etc/passwd', 'http://example.com:9000', 'http://user:pass@example.com'])
def test_private_import_blocked(url):
    with pytest.raises(ValueError):
        asyncio.run(lookup(url))


def test_private_dns_blocked():
    with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.1.1.1', 80))]):
        with pytest.raises(ValueError):
            asyncio.run(public_address('https://private.example'))


def test_name_lookup():
    result = asyncio.run(lookup('Brand Model 123'))
    assert result['specs'] == {'name': 'Brand Model 123'}
    assert 'provider' in result['message']


def test_fetch_pins_address_and_preserves_tls_hostname():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, headers={'content-type': 'text/html'}, text='<p>180 kWh / year</p>')
    original_client = httpx.AsyncClient
    with patch('wattwise.importer.public_address', new=AsyncMock(return_value='93.184.216.34')), patch('wattwise.importer.httpx.AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
        result = asyncio.run(lookup('https://shop.example/product'))
    assert result['specs'] == {'energy': 180, 'basis': 'year'}
    assert requests[0].url.host == '93.184.216.34'
    assert requests[0].headers['host'] == 'shop.example'
    assert requests[0].extensions['sni_hostname'] == 'shop.example'


def test_redirect_is_validated_again():
    original_client = httpx.AsyncClient
    def respond(request):
        return httpx.Response(302, headers={'location': 'http://127.0.0.1/secret'})
    with patch('wattwise.importer.public_address', new=AsyncMock(side_effect=['93.184.216.34', ValueError('Only public websites')])), patch('wattwise.importer.httpx.AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
        with pytest.raises(ValueError, match='public'):
            asyncio.run(lookup('https://shop.example/product'))
