"""Composable product extraction strategies, independent of HTTP and LLMs.

Each strategy emits evidence-bearing candidates. Conflicting measurements are
kept together (energy + basis), rather than mixing unrelated numbers and units.
"""
from dataclasses import dataclass, field
import json
import math
import re
import unicodedata

from bs4 import BeautifulSoup


@dataclass
class Candidate:
    field: str
    value: object
    source: str
    priority: int


@dataclass
class Extraction:
    specs: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def clean(value):
    return re.sub(r'\s+', ' ', str(value).replace('\xa0', ' ')).strip()


def normalized(value):
    return unicodedata.normalize('NFKD', clean(value)).encode('ascii', 'ignore').decode().lower()


def decimal(value, *, price=False):
    """Handle common European and English decimal/thousands conventions."""
    if isinstance(value, (float, int)):
        return float(value) if math.isfinite(value) and 0 <= value <= 10_000_000 else None
    tokens = re.findall(r'[+-]?\d[\d.,]*(?:[ \u00a0\u202f]\d{3}(?:[.,]\d+)*)*', str(value))
    if len(tokens) != 1:
        return None
    text = re.sub(r'[^\d.,+-]', '', tokens[0])
    if ',' in text and '.' in text:
        last = max(text.rfind(','), text.rfind('.'))
        text = re.sub(r'[.,]', '', text[:last]) + '.' + text[last + 1:]
    elif ',' in text:
        thousands = price and text.count(',') == 1 and len(text.rsplit(',', 1)[-1]) == 3 and text.split(',')[0] != '0'
        text = text.replace(',', '' if thousands else '.')
    elif text.count('.') > 1 or (price and '.' in text and len(text.rsplit('.', 1)[-1]) == 3 and text.split('.')[0] != '0'):
        text = text.replace('.', '')
    try:
        number = float(text)
        return number if math.isfinite(number) and 0 <= number <= 10_000_000 else None
    except ValueError:
        return None


ENERGY = re.compile(r'energy|electric|consumption|power|energie|verbruik|verbrauch|leistung', re.I)
BASIS_RULES = [
    ('100cycles', r'100\s*(?:(?:wash(?:ing)?|dry(?:ing)?)\s*)?(?:cycles?|cycli|cyclussen|zyklen|betriebszyklen)'),
    ('year', r'\b(?:year|annual|annum|jaar|jaarlijks|annuel|jahr|jahrlich|a\s*\))\b'),
    ('cycle', r'\b(?:cycle|cyclus|zyklus|wasbeurt)\b'),
    ('hour', r'\b(?:hour|uur|stunde)\b|/\s*h\b'),
]


def energy_measurement(label, value):
    context = normalized(f'{label} {value}')
    match = re.search(r'([\d][\d.,\s]*)\s*k\s*w\s*h\b', normalized(value))
    if match or ('kwh' in normalized(label).replace(' ', '') and decimal(value) is not None):
        amount = decimal(match[1]) if match else decimal(value)
        basis = next((basis for basis, pattern in BASIS_RULES if re.search(pattern, context)), None)
        if basis and amount is not None:
            return (amount, basis)
    # Electrical input power only. Never use thermal output or heating capacity.
    if re.search(r'input power|power input|rated power|vermogen|leistungsaufnahme', context) and not re.search(r'output|heating capacity|thermal', context):
        match = re.search(r'([\d][\d.,]*)\s*(kw|w)\b', normalized(value))
        if match:
            amount = decimal(match[1], price=match[2] == 'w')
            if amount is not None:
                return (amount / (1000 if match[2] == 'w' else 1), 'hour')
    return None


def efficiency(label, value):
    if not re.search(r'energy.*(?:class|rating|label|efficien)|energie.*(?:klasse|label|efficient)', normalized(label)):
        return None
    match = re.search(r'(?:^|[^a-z])([a-g]\+{0,3})(?:$|[^a-z+])', normalized(value))
    return match[1].upper() if match else None


def property_candidates(label, value, source, priority):
    label, value = clean(label), clean(value)
    if ENERGY.search(label):
        measure = energy_measurement(label, value)
        if measure:
            yield Candidate('measurement', measure, source, priority)
    grade = efficiency(label, value)
    if grade:
        yield Candidate('label', grade, source, priority)
    if re.search(r'^(?:price|sale price|prijs|preis)(?:\b|$)', normalized(label)):
        price = decimal(value, price=True)
        if price is not None:
            yield Candidate('price', price, source, priority)


def product_objects(soup):
    products = []
    def visit(obj, depth=0):
        if depth > 30:
            return
        if isinstance(obj, list):
            for item in obj:
                visit(item, depth + 1)
        elif isinstance(obj, dict):
            kind = obj.get('@type', [])
            if 'Product' in (kind if isinstance(kind, list) else [kind]):
                products.append(obj)
                return  # Do not select related products nested under a product.
            for key in ('@graph', 'mainEntity', 'itemListElement', 'item'):
                if key in obj:
                    visit(obj[key], depth + 1)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError, RecursionError):
            continue
    return products


def structured(soup):
    products = product_objects(soup)
    if not products:
        return
    heading = soup.find('h1') or soup.find('title')
    title = normalized(heading.get_text()) if heading else ''
    def score(obj):
        words = set(normalized(obj.get('name', '')).split())
        return len(words & set(title.split())) / max(len(words), 1)
    product = max(products, key=score)  # Main heading disambiguates recommendation carousels.
    source = 'Product structured data'
    if product.get('name'):
        yield Candidate('name', clean(product['name'])[:200], source, 100)
    offers = product.get('offers', {})
    offers = offers if isinstance(offers, list) else [offers]
    for offer in offers[:5]:
        if not isinstance(offer, dict):
            continue
        price_info = offer.get('priceSpecification', {})
        if isinstance(price_info, list):
            price_info = price_info[0] if price_info else {}
        if not isinstance(price_info, dict):
            price_info = {}
        price = decimal(offer.get('price', offer.get('lowPrice', price_info.get('price', ''))), price=True)
        if price is not None:
            yield Candidate('price', price, source, 100)
        currency = offer.get('priceCurrency', price_info.get('priceCurrency'))
        if currency:
            yield Candidate('currency', clean(currency)[:10], source, 100)
    props = product.get('additionalProperty', [])
    for prop in props if isinstance(props, list) else [props]:
        if isinstance(prop, dict):
            value = prop.get('value', '')
            unit = prop.get('unitText', prop.get('unitCode', ''))
            if isinstance(value, dict):
                unit = value.get('unitText', value.get('unitCode', unit))
                value = value.get('value', '')
            yield from property_candidates(prop.get('name', ''), f'{value} {unit}', source, 100)
    details = product.get('hasEnergyConsumptionDetails', {})
    if isinstance(details, dict):
        grade = str(details.get('hasEnergyEfficiencyCategory', '')).rsplit('/', 1)[-1]
        grade = re.sub(r'^EU?EnergyEfficiencyCategory', '', grade, flags=re.I)
        if re.fullmatch('[A-G]', grade):
            yield Candidate('label', grade, source, 100)


def metadata(soup):
    selectors = {
        'name': ['meta[property="og:title"]', '[itemprop="name"]'],
        'price': ['meta[property="product:price:amount"]', 'meta[property="og:price:amount"]', '[itemprop="price"]'],
        'currency': ['meta[property="product:price:currency"]', '[itemprop="priceCurrency"]'],
    }
    for field_name, queries in selectors.items():
        for selector in queries:
            node = soup.select_one(selector)
            if not node:
                continue
            value = node.get('content', node.get('value', node.get_text(' ', strip=True)))
            value = decimal(value, price=True) if field_name == 'price' else clean(value)[:200 if field_name == 'name' else 10]
            if value is not None and value != '':
                yield Candidate(field_name, value, 'Product metadata', 80)
                break
    heading = soup.find('h1')
    if heading:
        yield Candidate('name', clean(heading.get_text())[:200], 'Page heading', 60)


def specification_pairs(soup):
    for row in soup.select('tr'):
        cells = row.find_all(['td', 'th'], recursive=False)
        if len(cells) >= 2:
            yield clean(cells[0].get_text(' ', strip=True)), clean(cells[1].get_text(' ', strip=True)), 'Specification table'
    for dt in soup.select('dt'):
        dd = dt.find_next_sibling('dd')
        if dd:
            yield clean(dt.get_text(' ', strip=True)), clean(dd.get_text(' ', strip=True)), 'Specification list'
    # Many retailers use generic flex/grid divs rather than semantic tables.
    for row in soup.select('[data-spec], [class*="spec-row"], [class*="specification-row"], [class*="specification-item"], [class*="spec-item"], [class*="spec"] > div'):
        children = row.find_all(recursive=False)
        if len(children) == 2:
            yield clean(children[0].get_text(' ', strip=True)), clean(children[1].get_text(' ', strip=True)), 'Specification row'


def specifications(soup):
    for label, value, source in specification_pairs(soup):
        yield from property_candidates(label, value, source, 90)


def visible_text(soup):
    # Work in small blocks; global regexes can pair energy from another product.
    for node in soup.select('p, li, tr, dd, [data-spec], div, span'):
        if node.find(['div', 'p', 'li', 'table']):
            continue
        text = clean(node.get_text(' ', strip=True))
        if len(text) > 600:
            continue
        measure = energy_measurement(text, text)
        if measure:
            yield Candidate('measurement', measure, 'Visible page text', 50)
        match = re.search(r'(?:energy\s*(?:efficiency\s*)?(?:class|rating|label)|energie\s*(?:klasse|label|efficientieklasse))\s*[:：]?\s*([A-G]\+{0,3})\b', text, re.I)
        if match:
            yield Candidate('label', match[1].upper(), 'Visible page text', 50)




def embedded_state(soup):
    """Read JSON hydration state without executing retailer JavaScript."""
    products = []
    def visit(value, key='', depth=0):
        if depth > 25:
            return
        if isinstance(value, dict):
            if key.lower() in ('product', 'productdata', 'productdetails') and (value.get('name') or value.get('title')):
                products.append(value)
                return
            for child_key, child in value.items():
                if isinstance(child, (dict, list)):
                    visit(child, child_key, depth + 1)
        elif isinstance(value, list):
            for child in value[:50]:
                visit(child, key, depth + 1)
    for script in soup.select('script[type="application/json"], script#__NEXT_DATA__'):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError, RecursionError):
            pass
    if not products:
        return
    heading = soup.find('h1')
    words = set(normalized(heading.get_text()).split()) if heading else set()
    product = max(products, key=lambda p: len(set(normalized(p.get('name', p.get('title', ''))).split()) & words))
    source = 'Embedded product JSON'
    yield Candidate('name', clean(product.get('name', product.get('title')))[:200], source, 70)
    price = product.get('price')
    if isinstance(price, dict):
        price = price.get('amount', price.get('value'))
    price = decimal(price, price=True)
    if price is not None:
        yield Candidate('price', price, source, 70)
    currency = product.get('currency', product.get('priceCurrency'))
    if currency:
        yield Candidate('currency', clean(currency)[:10], source, 70)
    props = product.get('specifications', product.get('attributes', []))
    if isinstance(props, dict):
        props = [{'name': key, 'value': value} for key, value in props.items()]
    if isinstance(props, list):
        for prop in props:
            if isinstance(prop, dict):
                yield from property_candidates(prop.get('name', prop.get('label', '')), prop.get('value', ''), source, 70)


# Add strategies here without changing networking or orchestration.
STRATEGIES = (structured, metadata, specifications, embedded_state, visible_text)


def extract(html, strategies=None):
    soup = BeautifulSoup(html, 'html.parser')
    candidates = []
    for strategy in strategies or STRATEGIES:
        candidates.extend(strategy(soup))
    result = Extraction()
    for field_name in ('name', 'price', 'currency', 'label', 'measurement'):
        matches = sorted((c for c in candidates if c.field == field_name), key=lambda c: c.priority, reverse=True)
        if not matches:
            continue
        top = matches[0]
        conflicts = [c for c in matches if c.priority == top.priority and c.value != top.value]
        if conflicts:
            result.warnings.append(f'Conflicting {"energy measurements" if field_name == "measurement" else field_name} found; enter this field manually.')
            continue
        if field_name == 'measurement':
            result.specs.update(energy=top.value[0], basis=top.value[1])
            result.evidence['energy'] = result.evidence['basis'] = top.source
        else:
            result.specs[field_name] = top.value
            result.evidence[field_name] = top.source
    return result


def extract_specs(html):
    """Compatibility helper for callers that only need resolved fields."""
    return extract(html).specs


def page_text(html):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style', 'nav', 'footer', 'header']):
        node.decompose()
    return soup.get_text('\n', strip=True)[:35_000]
