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

Paste a public manufacturer or retailer product URL. The classic scraper combines independently extensible extraction strategies:

- Product JSON-LD, including additional properties, nested price specifications, and energy-class details.
- Open Graph and microdata product metadata.
- Specification tables, definition lists, and common div-based specification rows.
- JSON hydration state, including common `__NEXT_DATA__` product objects.
- Visible-text blocks with explicit consumption units.

It supports common English, Dutch, and German energy labels/units and European/English number formats. It keeps energy and its measurement basis together, prefers high-confidence structured evidence, and leaves conflicting equally ranked values empty for manual entry. The import form shows the origin of each field and any conflicts. Extend `scraping.STRATEGIES` with functions that yield `Candidate` objects; fetching and AI orchestration don't need changes.

Requests remain bounded by size and timeout. Public destination checks, pinned IP connections, and redirect validation protect against private-network imports. No network proxy environment variables are used for product fetching. JavaScript isn't executed: embedded product JSON can be read, but anti-bot pages and specs requiring live JavaScript may still fail. The importer doesn't bypass site restrictions or PDFs. All fields stay editable.

Brand/model text fills the name. Automatic model-name spec search isn't integrated. Missing values are left empty rather than guessed.

## Optional AI fallback and appliance research

An admin can configure an **OpenAI API key** and a Responses-compatible model with structured-output and web-search support. It uses the [Responses web-search API](https://developers.openai.com/api/docs/guides/tools-web-search). No additional SDK is required. Without a key, classic scraping and all cost calculations work as before.

For environment-based configuration (recommended for surviving restarts), set:

```bash
export WATTWISE_ADMIN_TOKEN='choose-a-long-random-admin-password'
export WATTWISE_LLM_API_KEY='your-openai-api-key'
export WATTWISE_LLM_MODEL='gpt-4.1-mini'
uv run uvicorn wattwise.main:app --host 0.0.0.0 --port 8000
```

`OPENAI_API_KEY` is also accepted if `WATTWISE_LLM_API_KEY` isn't set. This app reads process environment variables; it does not automatically load `.env` files.

Alternatively set only `WATTWISE_ADMIN_TOKEN`, open **Admin** (`/admin`), and authenticate with username **admin** and that token as the password. Enter an API key and model, or disable AI. Admin edits stay in server memory until restart; a blank key preserves the current value. Disabling AI overrides an environment-configured key for this process. Use HTTPS and `WATTWISE_SECURE_COOKIE=1` on a hosted instance. The admin endpoints require HTTP Basic authentication and CSRF protection; if no admin token is configured, they are disabled. Keys are never returned to clients, placed in sessions/snapshots, or included in error messages.

**Spec fallback:** the classic scraper runs first. Only unresolved fields from a readable product page are sent for AI-assisted extraction. Suggestions require supporting quotes found in the fetched text; numeric values and energy/basis pairs are checked. Accepted suggestions are labeled AI-assisted. AI doesn't replace classic results or resolve conflicting classic measurements by guessing. If the provider is unavailable, classic results and manual entry remain available. AI extraction cannot repair a page that the backend cannot fetch.

**Qualitative research:** when AI is enabled, each appliance automatically loads a separate panel covering common flaws, quirks, maintenance needs, parts/service availability, and owner feedback. OpenAI web search supplies source links; a second structured-output call formats those research notes without adding sources. Findings are shown only when their sources appear in the provider's search results/citations. Unsupported findings become “not enough sourced information”; exact-model uncertainty and series-level evidence are labeled. Owner anecdotes aren't failure-rate statistics. Enter a country/region in **Service market** for local parts/service research; otherwise local availability is not assumed.

Research is cached for 24 hours (up to 256 model/category/market/configuration combinations), duplicate in-flight requests are coalesced, and at most two research requests run concurrently. Each uncached report uses a web-search call followed by a structured-summary call. API calls are billed to the configured account. Page text is sent for spec fallback; appliance names, categories, and the service market are sent for research. No key means no AI requests. Invalid credentials, quota errors, refusals, or unsupported models produce an inline unavailable message rather than breaking the calculator.

**Best value is unchanged:** it uses purchase price + electricity cost, calculated from consumption, usage, rate, and years. Research never modifies numeric specs, adds guessed repair costs, or influences the winner. All imported facts remain editable and should be reviewed before saving.

Saved snapshots preserve the appliance facts and service market. Research is generated/cached separately and can be refreshed independently; it isn't a historical part of the snapshot. If AI is disabled later, saved numeric comparisons still work.

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

Tests cover calculation units and horizons, input validation, product extraction and blocked destinations, separate browser sessions, CSRF, session expiry, immutable shared snapshots, copying comparisons, persistence across session-store resets, HTML escaping, adaptable scraper layouts, admin authorization, AI fallback evidence, sourced research, failure handling, and unchanged numeric winners. AI/provider behavior is tested with mocked responses; a live provider account is required to verify model access and quotas.
