from copy import deepcopy
import os
from pathlib import Path
import secrets
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import state, ai
from .importer import lookup
from .models import Appliance, Comparison, calculate

ROOT = Path(__file__).parent
app = FastAPI(title='Wattwise — Appliance ownership calculator')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
templates = Jinja2Templates(directory=ROOT / 'templates')
CATEGORIES = ['Washing machine', 'Tumble dryer', 'Dishwasher', 'Heating / cooling', 'Heat pump', 'Refrigerator', 'Other appliance']
UNITS = {'100cycles': 'kWh / 100 cycles', 'cycle': 'kWh / cycle', 'hour': 'kWh / hour', 'year': 'kWh / year'}


@app.middleware('http')
async def browser_session(request: Request, call_next):
    if request.url.path.startswith('/static/'):
        return await call_next(request)
    key, current, fresh = state.get_session(request.cookies.get('wattwise_session'))
    request.state.session = current
    # JSON calculation API is stateless. All stateful UI mutations require CSRF.
    if request.method == 'POST' and request.url.path != '/api/compare':
        token = request.headers.get('X-CSRF-Token', '')
        if not secrets.compare_digest(token.encode(), current['csrf'].encode()):
            return HTMLResponse('Session expired. Refresh the page before trying again.', status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Vary'] = 'Cookie, HX-Request'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if fresh:
        response.set_cookie('wattwise_session', key, httponly=True,
                            secure=os.environ.get('WATTWISE_SECURE_COOKIE') == '1', samesite='lax')
    return response


def render(request, template, comparison=None, **extra):
    c = Comparison.model_validate(comparison if comparison is not None else request.state.session['comparison'])
    rows = calculate(c)
    for row in rows:
        row['example'] = any(all(row.get(k) == v for k, v in example.items()) for example in state.DEFAULTS['appliances'])
    best = min(rows, key=lambda r: r['total']) if rows else None
    worst = max(rows, key=lambda r: r['total']) if rows else None
    symbol = {'EUR': '€', 'USD': '$', 'GBP': '£'}[c.currency]
    return templates.TemplateResponse(request=request, name=template, context={
        'c': c, 'rows': rows, 'best': best, 'worst': worst,
        'saving': worst['total'] - best['total'] if rows else 0,
        'max_total': max([row['total'] for row in rows] + [1]),
        'different_categories': len({row['category'] for row in rows}) > 1,
        'csrf': request.state.session['csrf'], 'categories': CATEGORIES, 'units': UNITS,
        'ai_enabled': ai.settings().enabled, 'admin_enabled': bool(os.environ.get('WATTWISE_ADMIN_TOKEN')),
        'money': lambda n: f'{symbol}{n:,.2f}',
        'num': lambda n: f'{n:,.1f}'.rstrip('0').rstrip('.'),
        **extra,
    })


async def fields(request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16_000:
            raise HTTPException(413, 'Form too large')
    try:
        return {k: v[-1] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}
    except UnicodeDecodeError as exc:
        raise HTTPException(400, 'Invalid form encoding') from exc


@app.get('/')
async def home(request: Request):
    return render(request, 'index.html')


@app.post('/api/compare')
async def compare(data: Comparison):
    return {'results': calculate(data)}


@app.post('/settings')
async def settings(request: Request):
    data = await fields(request)
    c = request.state.session['comparison']
    try:
        updated = Comparison.model_validate({**c, **{k: data[k] for k in ('rate', 'currency', 'market') if k in data}, 'years': int(data.get('years', c['years']))})
    except (ValidationError, ValueError):
        return render(request, 'workspace.html', error='Enter a valid electricity rate, currency, and comparison period.')
    changed_currency = updated.currency != c['currency']
    request.state.session['comparison'] = updated.model_dump()
    return render(request, 'workspace.html', notice='Currency display changed. Amounts are not converted.' if changed_currency else None)


@app.get('/appliances/new')
async def new_appliance(request: Request):
    return render(request, 'editor.html', a=dict(name='', category='Washing machine', price='', energy='', basis='100cycles', usage=4, label=''), index=None)


@app.get('/appliances/{index}/edit')
async def edit_appliance(request: Request, index: int):
    items = request.state.session['comparison']['appliances']
    if not 0 <= index < len(items):
        raise HTTPException(404, 'Appliance not found')
    return render(request, 'editor.html', a=items[index], index=index)


@app.post('/appliances')
async def save_appliance(request: Request):
    data = await fields(request)
    index = data.pop('index', '')
    try:
        appliance = Appliance.model_validate(data)
        items = request.state.session['comparison']['appliances']
        if index == '':
            if len(items) >= 12:
                raise ValueError('You can compare up to 12 appliances.')
            items.append(appliance.model_dump())
        else:
            index = int(index)
            if not 0 <= index < len(items):
                raise ValueError('This appliance no longer exists. Refresh your comparison.')
            items[index] = appliance.model_dump()
    except (ValueError, ValidationError):
        return render(request, 'editor.html', a=data, index=index if index != '' else None,
                      error='Check the values: prices and energy must be nonnegative; hours per day must be 0–24. You can compare up to 12 appliances.')
    response = render(request, 'workspace.html')
    response.headers.update({'HX-Retarget': '#workspace', 'HX-Reswap': 'outerHTML', 'HX-Trigger': 'closeEditor'})
    return response


@app.post('/appliances/{index}/remove')
async def remove_appliance(request: Request, index: int):
    items = request.state.session['comparison']['appliances']
    if not 0 <= index < len(items):
        raise HTTPException(404, 'Appliance not found')
    items.pop(index)
    return render(request, 'workspace.html')


@app.post('/reset')
async def reset(request: Request):
    request.state.session['comparison'] = deepcopy(state.DEFAULTS)
    return render(request, 'workspace.html', notice='Comparison reset to editable examples.')


@app.post('/lookup')
async def lookup_form(request: Request):
    data = await fields(request)
    result = {}
    try:
        result = await lookup(data.get('query', ''))
        data.update({k: v for k, v in result['specs'].items() if k in ('name', 'price', 'energy', 'basis', 'label')})
        message = result['message']
        source_currency = result['specs'].get('currency')
        if source_currency and source_currency != request.state.session['comparison']['currency']:
            message += f' Source price is in {source_currency}; convert it to your selected currency before saving.'
    except ValueError as exc:
        message = str(exc)
    return render(request, 'editor.html', a=data, index=data.get('index') or None, message=message,
                  evidence=result.get('evidence', {}), warnings=result.get('warnings', []), source_url=result.get('source_url'))


@app.post('/save')
async def save_shared(request: Request):
    data = await fields(request)
    title = data.get('title', '').strip()[:120] or 'Appliance comparison'
    key = state.save_snapshot(request.state.session['comparison'], title)
    return render(request, 'share.html', shared_id=key, title=title)


@app.get('/saved')
async def saved(request: Request):
    return render(request, 'saved.html', snapshots=state.list_snapshots())


@app.get('/shared/{key}')
async def shared(request: Request, key: str):
    snapshot = state.get_snapshot(key)
    if not snapshot:
        raise HTTPException(404, 'Saved comparison not found')
    return render(request, 'index.html', comparison=snapshot['comparison'], read_only=True, snapshot=snapshot)


@app.post('/shared/{key}/load')
async def load_shared(request: Request, key: str):
    snapshot = state.get_snapshot(key)
    if not snapshot:
        raise HTTPException(404, 'Saved comparison not found')
    request.state.session['comparison'] = deepcopy(snapshot['comparison'])
    return HTMLResponse('', headers={'HX-Redirect': '/'})


basic = HTTPBasic(auto_error=False)


def require_admin(credentials: HTTPBasicCredentials | None = Depends(basic)):
    expected = os.environ.get('WATTWISE_ADMIN_TOKEN', '')
    if not expected:
        raise HTTPException(404, 'Admin settings are disabled. Set WATTWISE_ADMIN_TOKEN on the server.')
    if credentials is None or not secrets.compare_digest(credentials.username.encode(), b'admin') or not secrets.compare_digest(credentials.password.encode(), expected.encode()):
        raise HTTPException(401, 'Admin authentication required', headers={'WWW-Authenticate': 'Basic realm="Wattwise admin"'})


@app.get('/admin', dependencies=[Depends(require_admin)])
async def admin(request: Request):
    return render(request, 'admin.html', model=ai.settings().model)


@app.post('/admin/ai', dependencies=[Depends(require_admin)])
async def admin_ai(request: Request):
    data = await fields(request)
    try:
        ai.configure(api_key=data.get('api_key', '').strip() or None,
                     model=data.get('model', '').strip(), disable=data.get('disable') == 'yes')
        message = 'AI disabled.' if data.get('disable') == 'yes' else 'AI configuration updated. Reload your comparison to load appliance research.'
    except ValueError as exc:
        message = str(exc)
    return render(request, 'admin_settings.html', model=ai.settings().model, message=message)


async def research_fragment(request, item, market):
    if not ai.settings().enabled:
        return render(request, 'research.html', report=None, research_error='Appliance research is disabled.')
    try:
        report = await ai.research(item['name'], item.get('category', ''), market)
        return render(request, 'research.html', report=report)
    except ai.AIUnavailable as exc:
        return render(request, 'research.html', report=None, research_error=str(exc))


@app.get('/appliances/{index}/research')
async def appliance_research(request: Request, index: int):
    c = request.state.session['comparison']
    if not 0 <= index < len(c['appliances']):
        raise HTTPException(404, 'Appliance not found')
    return await research_fragment(request, c['appliances'][index], c.get('market', ''))


@app.get('/shared/{key}/appliances/{index}/research')
async def shared_research(request: Request, key: str, index: int):
    item = state.get_snapshot(key)
    if not item or not 0 <= index < len(item['comparison']['appliances']):
        raise HTTPException(404, 'Appliance not found')
    c = item['comparison']
    return await research_fragment(request, c['appliances'][index], c.get('market', ''))
