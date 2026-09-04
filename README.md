# Kestrel control tower

One screen for where Kestrel Provisions is losing service and money, with a
plain-English question box. Built for the FDE take-home. Read `DECISIONS.md`
first, then `docs/DATA_NOTES.md` for the data findings and what was done about
each.

## Run it

You need Python 3.11 or newer and the assignment pack (for the database and,
optionally, the competitor site and partner API).

```bash
git clone <this repo> kestrel-control-tower
cd kestrel-control-tower
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# point the app at the database (not committed). Either copy it in...
cp /path/to/FDE_Assignment_Pack/data/kestrel_ops.db data/
# ...or set an env var
export KESTREL_DB=/path/to/FDE_Assignment_Pack/data/kestrel_ops.db

streamlit run app.py
```

That opens http://localhost:8501 with the service, cold chain and returns
screens working from the database alone. Two tabs need data pulled from the
external sources first:

**Freight cost per case** (Money tab) comes from the mock carrier billing API.

```bash
# in the assignment pack, in another terminal
pip install fastapi uvicorn && python partner_api/server.py      # port 8088

# back here
python scripts/sync_freight.py            # last complete fiscal quarter, ~1-2 min
python scripts/sync_freight.py --all      # every invoice, several minutes
```

**Price position** comes from the BazaarPulse site.

```bash
# in the assignment pack, in another terminal
cd bazaarpulse_site && python3 -m http.server 8080

# back here
python scripts/scrape_prices.py           # ~70s, honours the 1s crawl delay
```

Both scripts write to `data/cache.db`, are safe to re-run, and resume if
interrupted. Delete `data/cache.db` to start over. The app picks the data up
on the next page load.

**Ask tab.** Needs an LLM. Any one of these works; without one the tab
offers eight prepared questions.

```bash
export ANTHROPIC_API_KEY=...                 # Claude (model via KESTREL_LLM_MODEL)
export GEMINI_API_KEY=...                    # Gemini, free tier at aistudio.google.com/apikey
# or any OpenAI-compatible endpoint, e.g. Groq (free tier) or a local Ollama:
export LLM_API_KEY=... LLM_BASE_URL=https://api.groq.com/openai/v1 LLM_MODEL=llama-3.3-70b-versatile
export LLM_API_KEY=ollama LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.1
```

The model only ever writes a single SELECT; it is checked for anything
else before it runs, and the SQL is shown with every answer.

## Knobs

All optional, all environment variables.

| Variable | Default | What it does |
|---|---|---|
| `KESTREL_DB` | `data/kestrel_ops.db` | Path to the operational database (opened read-only) |
| `KESTREL_CACHE` | `data/cache.db` | Where freight invoices and competitor prices are stored |
| `KESTREL_ONTIME_MIN` | `30` | A delivery is on time if `delay_minutes` is at or below this |
| `KESTREL_IN_FULL_PCT` | `90` | An order is in full if its case fill rate is at or above this |
| `KESTREL_PRICE_FRESHNESS_DAYS` | `14` | Ignore competitor listings last seen longer ago than this |
| `KESTREL_PARTNER_API` | `http://localhost:8088` | Partner API base URL |
| `KESTREL_PARTNER_API_KEY` | the key from the pack | Sent as `X-API-Key` |
| `KESTREL_BAZAAR_URL` | `http://localhost:8080` | Competitor site base URL |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or `LLM_API_KEY`+`LLM_BASE_URL`+`LLM_MODEL` | none | Enables free-form questions in the Ask tab |

## Tests

```bash
pytest            # ~40s; the app boots headlessly as one of the tests
```

Tests that need the database skip themselves when it is absent.

## Layout

```
app.py                  the Streamlit app
kestrel/
  config.py             paths and knobs
  db.py                 read-only connection, cache attach, view creation
  sql/views.sql         the clean layer (temp views)
  dates.py              timestamp parsing, fiscal calendar
  metrics.py            every number on the screen
  partner_api.py        billing API client: retry, resume, paise, UTC
  scraper.py            BazaarPulse crawler, four price parsers
  matching.py           listing title -> Kestrel SKU
  ask.py                question -> SQL -> rows -> short answer
scripts/
  sync_freight.py       pull invoices into the cache
  scrape_prices.py      crawl the site into the cache
  profile.py            re-run the data checks behind docs/DATA_NOTES.md
docs/DATA_NOTES.md           data findings, decisions, alternatives
tests/
```

The database is never modified. All cleaning happens in temporary views
created when the app connects, so there is no build step and nothing to keep
in sync.
