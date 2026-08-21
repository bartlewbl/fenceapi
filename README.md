# fenceapi

Open-source JSON API and scraper for fencing tournaments from
[Fencing Worldwide](https://www.fencingworldwide.com) and
[Ophardt](https://fencing.ophardt.online/en/calendar).

This data is collected and used by the creators of [Boutfence](https://boutfence.com).
The project is also free for anyone else who needs structured calendar, entry,
ranking, and club data. The upstream sites are server-rendered HTML; fenceapi
fetches them politely, caches the result in SQLite, and serves JSON.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-0E7C3A)](LICENSE)
[![Rate limit](https://img.shields.io/badge/rate%20limit-100%2Fmin%20per%20IP-4C6EF5)](#rate-limits)

---

## Public API

**Base URL:** [https://api.boutfence.com](https://api.boutfence.com)

The hosted API is free to use. Please keep to **100 requests per minute per IP**
so one client cannot crowd everyone else out.

```bash
curl "https://api.boutfence.com/v1/calendar?nation=GER"
curl "https://api.boutfence.com/v1/events/34860"
curl "https://api.boutfence.com/v1/rankings/ger/epee/men/senior"
```

Interactive OpenAPI docs: `https://api.boutfence.com/docs`

From the browser or Boutfence:

```js
const events = await fetch("https://api.boutfence.com/v1/calendar?nation=GER")
  .then((r) => r.json());
```

Calendar, event, and current endpoints scrape Ophardt / Fencing Worldwide **only on
a cache miss**. Rankings and clubs are read from a local SQLite database and are
never scraped on a GET.

## Rate limits

| | |
| --- | --- |
| Limit | **100 requests / minute / IP** |
| Window | Sliding 60 seconds |
| Over limit | `429 Too Many Requests` |
| Exempt | `/`, `/docs`, `/redoc`, `/openapi.json`, `/v1/health` |

Successful responses include:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 42
```

When you exceed the cap:

```json
{
  "error": "rate_limit_exceeded",
  "limit": 100,
  "window_seconds": 60,
  "retry_after": 12
}
```

Wait for `Retry-After` seconds (also sent as a header) before trying again. The
limiter keys on the leftmost `X-Forwarded-For` address when the API sits behind
Caddy or nginx.

Self-hosters can change the cap with `FENCEAPI_RATE_LIMIT`, `FENCEAPI_RATE_WINDOW`,
or `python -m fenceapi serve --rate-limit 100`. Use `--rate-limit 0` to disable it
on a private machine.

## Endpoints

| Method | Path | Source |
| --- | --- | --- |
| `GET` | `/v1/health` | Cache stats (not rate-limited) |
| `GET` | `/v1/calendar?nation=GER` | Ophardt calendar (cached ~30 min) |
| `GET` | `/v1/events/34860` | Tournament info |
| `GET` | `/v1/events/34860/entries` | Public entries list |
| `GET` | `/v1/current?nation=ger` | Fencing Worldwide home |
| `GET` | `/v1/rankings` | Federations in the rankings DB |
| `GET` | `/v1/rankings/ger` | Category catalog |
| `GET` | `/v1/rankings/ger/epee/men/senior` | Stored ranking list |
| `GET` | `/v1/clubs` | Club list |

CORS is open for `boutfence.com` and localhost. Set `FENCEAPI_CORS` to a
comma-separated origin list if you need more. Set `FENCEAPI_API_KEY` to require
`X-API-Key` on data routes (`/docs` stays public).

---

## Self-host

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,api]"
```

Warm the calendar cache, then serve JSON:

```bash
python -m fenceapi sync-calendar
python -m fenceapi serve --host 127.0.0.1 --port 8000
```

Local docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### systemd

Copy the units from `deploy/`, edit `WorkingDirectory` / `ExecStart` to your
checkout, then:

```bash
sudo cp deploy/fenceapi.service deploy/fenceapi-sync.service deploy/fenceapi-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fenceapi.service fenceapi-sync.timer
```

Put Caddy or nginx in front for HTTPS. Point a hostname at this machine (port
forward 443, or Tailscale / Cloudflare Tunnel if you do not want to expose the
origin IP). Keep the 1 second upstream scrape interval — the API can handle many
readers; only cache refreshes hit Ophardt.

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `FENCEAPI_RATE_LIMIT` | `100` | Requests per IP per window (`0` disables) |
| `FENCEAPI_RATE_WINDOW` | `60` | Window length in seconds |
| `FENCEAPI_INTERVAL` | `1.0` | Seconds between upstream scrapes |
| `FENCEAPI_CORS` | Boutfence + localhost | Allowed browser origins |
| `FENCEAPI_API_KEY` | unset | Require `X-API-Key` on data routes |
| `FENCEAPI_EVENTS_DB` | `data/events.sqlite` | Calendar / event cache |
| `FENCEAPI_RANKINGS_DB` | `data/rankings.sqlite` | Rankings and clubs |

---

## CLI scraper

```bash
# Current / upcoming / recently archived tournaments
python -m fenceapi current
python -m fenceapi current --nation ger

# Tournament overview + competitions
python -m fenceapi tournament 33940-2026

# Competition pages (use competitions[].resource.key from the tournament payload)
python -m fenceapi results 916515-2025
python -m fenceapi participants 916515-2025
python -m fenceapi timetable 916515-2025

# Full year archive
python -m fenceapi archive 2026

# Ophardt calendar + tournament info
python -m fenceapi calendar
python -m fenceapi calendar GER --weapon epee --from 2026-08-01 --to 2026-12-31
python -m fenceapi event 34860
python -m fenceapi event 34860 --entries
python -m fenceapi calendar GER --details --limit 5
python -m fenceapi sync-calendar GER --details --limit 20

# Rankings (federation × weapon × gender × age)
python -m fenceapi rankings
python -m fenceapi rankings ger
python -m fenceapi rankings ger epee men senior
python -m fenceapi rankings ger --all
python -m fenceapi rankings ger --season 2025 --all
python -m fenceapi rankings --all-regions --all-seasons
python -m fenceapi rankings fie foil women u20
python -m fenceapi clubs
python -m fenceapi clubs -o data/clubs.json

# HTTP API on this machine
python -m fenceapi serve --host 127.0.0.1 --port 8000
```

`--all` downloads every matching published ranking list. Current-season DFB often
only has a subset of ages published; use `--season 2025` for a fuller grid.

The older nation JSON widget is still available as
`python -m fenceapi calendar GER --json-widget`.

### Python

```python
from fenceapi import Scraper

scraper = Scraper()
home = scraper.home()
tournament = scraper.tournament("33940-2026")
results = scraper.results("916515-2025")
```

Requests to upstream sites are spaced **1 second** apart by default. Override with
`--interval 0.5` or `HttpClient(min_interval=1.0)`.

---

## What the sites expose

| URL | Data |
| --- | --- |
| `/en/` and `/en/{nation}` | Current, upcoming, and recent tournaments |
| `/en/{eventId}-{year}/tournament/` | Event overview and competition list |
| `/en/{compId}-{year}/results/` | Final ranking |
| `/en/{compId}-{year}/participants/` | Start list |
| `/en/{compId}-{year}/timetable/` | Schedule |
| `/en/archive/{year}` | Year archive |
| `fencing.ophardt.online/en/calendar` | Upcoming tournaments (filterable) |
| `/en/widget/event/{id}` | Tournament info, competitions, invitation |
| `/en/inscriptions/show/{id}` | Public entries list |

Rankings live on [ophardt.online](https://fencing.ophardt.online/en/search/rankings).
Competition IDs are not the same as event IDs — always take
`competitions[].resource.key` from the tournament payload.

Status icons on Fencing Worldwide map to:

- `current` — live / current results
- `transferred` — results uploaded, not live
- `no_data` — listed, no results yet
- `live_feed` — scoring machines are transmitting
- `external` — hosted elsewhere (FencingTime Live, etc.)
- `archive` — finished event

## Courtesy

`robots.txt` on the upstream sites allows crawling (`Allow: /`). Identify this
client, keep the request interval, and do not hammer result pages during a live
tournament.

## License

[MIT](LICENSE). Contributions and independent deployments are welcome.
