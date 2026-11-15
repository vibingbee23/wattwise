"""Bounded HTTP fetching and composable classic/optional AI import pipeline."""
import asyncio
from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from . import ai
from .scraping import Extraction, extract, extract_specs, page_text


class UnsafeURL(ValueError):
    """A destination must never be forwarded to an AI fallback."""


async def public_address(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.port not in (None, 80, 443):
        raise UnsafeURL('Use a public http or https product URL.')
    addresses = await asyncio.wait_for(asyncio.to_thread(
        socket.getaddrinfo, parts.hostname,
        parts.port or (443 if parts.scheme == 'https' else 80),
        type=socket.SOCK_STREAM,
    ), timeout=5)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise UnsafeURL('Only public product websites can be imported.')
    return addresses[0][4][0]


@dataclass
class ProductPage:
    url: str
    extraction: Extraction
    text: str


async def _fetch_product(url):
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
        for _ in range(4):
            address = await public_address(url)
            original = httpx.URL(url)
            async with client.stream('GET', original.copy_with(host=address),
                    headers={'Host': original.netloc.decode(), 'User-Agent': 'Wattwise/0.2 product specification reader', 'Accept': 'text/html,application/xhtml+xml'},
                    extensions={'sni_hostname': original.host}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                if not any(kind in response.headers.get('content-type', '') for kind in ('text/html', 'application/xhtml+xml')):
                    raise ValueError('This URL does not contain a product webpage.')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise ValueError('This product page is too large to import.')
                html = body.decode(response.encoding or 'utf-8', errors='replace')
                return ProductPage(url, extract(html), page_text(html))
        raise ValueError('Too many redirects. Paste the final product URL.')


async def lookup(query):
    query = query.strip()
    if not query or len(query) > 2048:
        raise ValueError('Enter a product name or URL, up to 2048 characters.')
    if '://' not in query:
        return {'specs': {'name': query[:200]}, 'message': 'Name added. Paste a product URL to import published specs, or enter the figures below. Automatic model search needs a product-data provider.', 'evidence': {}, 'warnings': []}
    try:
        page = await asyncio.wait_for(_fetch_product(query), timeout=20)
    except UnsafeURL:
        raise
    except (httpx.HTTPError, OSError, TimeoutError) as exc:
        raise ValueError('Could not read this product page. Try another URL or enter the specs manually.') from None
    result = page.extraction
    missing = {'name', 'price', 'currency', 'energy', 'basis', 'label'} - result.specs.keys()
    if ai.settings().enabled and missing and not result.warnings:
        try:
            suggestions, evidence = await ai.fill_specs(page.text, missing)
            for key, value in suggestions.items():
                if key in missing:
                    result.specs[key] = value
                    result.evidence[key] = evidence[key]
        except ai.AIUnavailable as exc:
            result.warnings.append(str(exc))
    message = 'Imported available fields. Check the price, source currency, and energy units before saving.' if result.specs else 'No supported specs found. Enter the details manually below.'
    return {'specs': result.specs, 'message': message, 'evidence': result.evidence,
            'warnings': result.warnings, 'source_url': page.url}
