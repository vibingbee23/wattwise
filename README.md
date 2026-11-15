# Wattwise

Compare the real cost of home appliances with **FastAPI, Jinja, and HTMX**. Editable example washing machines make it easy to start; add up to 12 appliances and compare purchase prices, energy consumption, and total ownership costs over 5 and 10 years.

## Run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync --locked
uv run uvicorn wattwise.main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. JSON calculation API docs: http://localhost:8000/docs.

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. Development dependencies are included by default. Hatchling builds the package and includes Jinja templates, JavaScript, CSS, and vendored HTMX 2.0.8 with its license.

## Calculations

| Consumption basis | Annual electricity consumption |
| --- | --- |
| kWh per cycle | kWh × cycles/week × 52 |
| kWh per 100 cycles | kWh ÷ 100 × cycles/week × 52 |
| kWh per hour (electrical input kW) | kW × hours/day × 365 |
| kWh per year | Entered annual figure directly |

Annual electricity cost = annual kWh × electricity rate. Total ownership cost = purchase price + annual electricity cost × years. The comparison shows annual energy, energy over the selected period, electricity cost, total ownership cost, and savings compared with the most expensive option.

Energy labels are descriptive; calculations use the consumption you enter. Compare equivalent capacities and usage for a fair purchasing decision. For heat pumps use electrical input rather than heat output; weather and seasonal efficiency are not modeled. Estimates exclude maintenance, water, installation, inflation, and standby consumption unless included in your energy figure. Currency selection changes display, not exchange rates.

## Importing product specs

Paste a public manufacturer or retailer product URL. The importer reads schema.org Product JSON-LD for names and prices, plus explicit kWh units and energy classes in page text. It doesn't execute JavaScript, so some stores will not work. All fields remain editable. Review imported figures, units, and source currency before saving.

Requests are bounded by size and timeout. Public destination checks, pinned IP connections, and redirect validation protect against imports from private network addresses. No network proxy environment variables are used for product fetching.

Brand/model text fills the name. **Automatic model search requires a product-data provider and is not integrated.** Missing values are never fabricated; enter them manually or try another product URL.

## Browser sessions and saving

- Each browser gets an opaque, random HTTP-only session cookie with SameSite=Lax. Drafts live in server memory, isolated by that cookie; appliance data is not stored in cookies or local storage. Tabs in the same browser share a session.
- Session cookies expire with the browser session. Server drafts expire after 24 hours of inactivity or on process restart. Stateful UI requests require a per-session CSRF token.
- **Save & share** creates an immutable SQLite snapshot and returns a `/shared/<id>` link. **Saved comparisons** lists the latest 50 snapshots, visible to everyone with access to the app. Save only comparisons you intend to share.
- Viewing a snapshot does not change the viewer's draft. **Use a copy in my session** copies it into their own draft. Their edits don't change your draft or the saved snapshot. Saving again creates a new snapshot.

The database defaults to `.data/wattwise.sqlite3`, relative to the working directory. Set `WATTWISE_DB` to choose another path, and back up that file to retain shared comparisons. Drafts disappear on restart; saved comparisons remain.

Run **one worker/process** with the memory session store. Multiple workers/replicas need a shared session store first. For HTTPS hosting set `WATTWISE_SECURE_COOKIE=1`. Share links use the current app origin; sharing across devices requires a reachable server. This project is source code for a Python ASGI host, not a deployed service.

HTMX handles appliance editing, imports, deletion, settings, period selection, saving, and browsing shared comparisons through server-rendered Jinja fragments. Small JS helpers handle dialogs, labels, and the clipboard. JavaScript is required. Fonts load from Google Fonts with local sans-serif fallbacks.

## Verify and build

```bash
uv run pytest -q
node --check src/wattwise/static/app.js
uv build
```

Tests cover calculation units and horizons, input validation, product extraction and blocked destinations, separate browser sessions, CSRF, session expiry, immutable shared snapshots, copying comparisons, persistence across session-store resets, and HTML escaping.
