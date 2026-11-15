from pathlib import Path
import pytest
from wattwise.scraping import Candidate, decimal, energy_measurement, extract, extract_specs

FIXTURES = Path(__file__).parent / 'fixtures'


def test_localized_table_and_metadata():
    result = extract((FIXTURES / 'retailer_table.html').read_text())
    assert result.specs == {'name': 'Acme EcoWash 900', 'price': 1299.99, 'currency': 'EUR', 'label': 'A', 'energy': 49, 'basis': '100cycles'}
    assert result.evidence['energy'] == 'Specification table'


def test_main_product_and_additional_properties():
    result = extract_specs((FIXTURES / 'manufacturer_properties.html').read_text())
    assert result == {'name': 'Acme Dryer DX100', 'price': 799.99, 'currency': 'EUR', 'label': 'B', 'energy': 80, 'basis': '100cycles'}


@pytest.mark.parametrize('markup,expected', [
    ('<dl><dt>Annual energy consumption</dt><dd>180 kWh</dd></dl>', (180, 'year')),
    ('<div class="specification-row"><span>Energy per cycle</span><span>0,249 kWh</span></div>', (.249, 'cycle')),
    ('<dl><dt>Rated power input</dt><dd>1.500 W</dd></dl>', (1.5, 'hour')),
    ('<p>0.42 kWh / cycle</p>', (.42, 'cycle')),
    ('<table><tr><th>Energieverbrauch pro 100 Betriebszyklen</th><td>50 kWh</td></tr></table>', (50, '100cycles')),
])
def test_layouts_and_units(markup, expected):
    result = extract_specs(markup)
    assert (result['energy'], result['basis']) == expected


def test_no_assumed_basis_or_thermal_output():
    assert energy_measurement('Energy consumption', '49 kWh') is None
    assert extract_specs('<dl><dt>Heating capacity thermal output</dt><dd>8 kW</dd></dl>') == {}
    assert extract_specs('<dl><dt>Weight</dt><dd>80 kg</dd></dl>') == {}


def test_conflicting_measurements_are_not_combined():
    result = extract('<table><tr><td>Annual energy consumption</td><td>180 kWh</td></tr><tr><td>Energy per 100 cycles</td><td>49 kWh</td></tr></table>')
    assert 'energy' not in result.specs and 'basis' not in result.specs
    assert 'Conflicting energy' in result.warnings[0]


def test_custom_extractor_extension():
    def store_specific(soup):
        yield Candidate('name', soup.select_one('[data-model]')['data-model'], 'Store adapter', 110)
    result = extract('<div data-model="Model X"></div>', strategies=[store_specific])
    assert result.specs == {'name': 'Model X'}
    assert result.evidence == {'name': 'Store adapter'}


def test_decimal_conventions():
    assert decimal('€ 1.299,99', price=True) == 1299.99
    assert decimal('$1,299.99', price=True) == 1299.99
    assert decimal('0,249') == .249
    assert decimal('1.299', price=True) == 1299
    assert decimal('NaN') is None


def test_units_in_label_and_split_fields():
    result = extract_specs('<div class="specifications"><div><span>Energy consumption (kWh/100 cycles)</span><span>49</span></div></div>')
    assert (result['energy'], result['basis']) == (49, '100cycles')
    assert extract_specs('<div>Energy consumption: 49 kWh per 100 cycles</div>')['energy'] == 49


def test_embedded_application_json():
    html = '''<h1>Model Q</h1><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"product":{"title":"Model Q","price":{"amount":"599,99"},"currency":"EUR","specifications":[{"label":"Annual energy consumption","value":"180 kWh"}]}}}}</script>'''
    result = extract(html)
    assert result.specs == {'name': 'Model Q', 'price': 599.99, 'currency': 'EUR', 'energy': 180, 'basis': 'year'}
    assert result.evidence['energy'] == 'Embedded product JSON'


def test_price_with_other_numbers_is_not_silently_concatenated():
    assert decimal('€699 including 21% VAT', price=True) is None
    assert decimal('€1 299,99', price=True) == 1299.99
