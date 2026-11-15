"""Best-effort, bounded HTML product importer. Never invents missing specs."""
import asyncio
import ipaddress
import json
import math
import re
import socket
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import httpx


async def public_address(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError('Use a public http or https product URL.')
    addresses = await asyncio.wait_for(asyncio.to_thread(
        socket.getaddrinfo, parts.hostname,
        parts.port or (443 if parts.scheme == 'https' else 80),
        type=socket.SOCK_STREAM,
    ), timeout=5)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Only public product websites can be imported.')
    return addresses[0][4][0]


def extract_specs(html):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            types = value.get('@type', [])
            if 'Product' in (types if isinstance(types, list) else [types]):
                if value.get('name'):
                    found['name'] = str(value['name'])[:200]
                offers = value.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    try:
                        price = float(offers.get('price', offers.get('lowPrice')))
                        if math.isfinite(price) and 0 <= price <= 10_000_000:
                            found['price'] = price
                    except (ValueError, TypeError):
                        pass
                    if offers.get('priceCurrency'):
                        found['currency'] = str(offers['priceCurrency'])[:10]
            for child in value.values():
                if isinstance(child, (list, dict)):
                    visit(child)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError, RecursionError):
            pass
    for node in soup(['script', 'style', 'nav', 'footer']):
        node.decompose()
    text = soup.get_text(' ', strip=True)
    match = re.search(r'(\d+(?:[.,]\d+)?)\s*kWh\s*(?:/|per\s*)\s*(100\s*cycles|cycle|annum|year|h(?:our)?)\b', text, re.I)
    if match:
        energy = float(match[1].replace(',', '.'))
        if math.isfinite(energy) and energy <= 10_000_000:
            found['energy'] = energy
            unit = match[2].lower()
            found['basis'] = '100cycles' if '100' in unit else 'cycle' if unit == 'cycle' else 'year' if unit in ('annum', 'year') else 'hour'
    match = re.search(r'energy\s*(?:efficiency\s*)?(?:class|rating)\s*[:：]?\s*(A\+{0,3}|[B-G])\b', text, re.I)
    if match:
        found['label'] = match[1].upper()
    return found


async def _fetch_product(url):
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            address = await public_address(url)
            original = httpx.URL(url)
            # Connect to the vetted address, preserving Host and TLS SNI.
            async with client.stream('GET', original.copy_with(host=address),
                    headers={'Host': original.netloc.decode(), 'User-Agent': 'Wattwise/0.1 product specification reader'},
                    extensions={'sni_hostname': original.host}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                if 'text/html' not in response.headers.get('content-type', ''):
                    raise ValueError('This URL does not contain a product webpage.')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ValueError('This product page is too large to import.')
                return extract_specs(body.decode('utf-8', errors='replace'))
        raise ValueError('Too many redirects. Paste the final product URL.')


async def lookup(query):
    query = query.strip()
    if not query or len(query) > 2048:
        raise ValueError('Enter a product name or URL, up to 2048 characters.')
    if '://' not in query:
        return {'specs': {'name': query[:200]}, 'message': 'Name added. Paste a product URL to import published specs, or enter the figures below. Automatic model search needs a product-data provider.'}
    try:
        specs = await asyncio.wait_for(_fetch_product(query), timeout=20)
    except (httpx.HTTPError, OSError, TimeoutError) as exc:
        raise ValueError('Could not read this product page. Try another URL or enter the specs manually.') from exc
    return {'specs': specs, 'message': 'Imported available fields. Check the price, source currency, and energy units before saving.' if specs else 'No supported specs found. Enter the details manually below.'}
