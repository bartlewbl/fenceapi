# fenceapi

Open-source JSON API and scraper for fencing tournaments from
[Fencing Worldwide](https://www.fencingworldwide.com) and
[Ophardt](https://fencing.ophardt.online/en/calendar).

This data is collected and used by the creators of [Boutfence](https://boutfence.com).
The project is also free for anyone else who needs structured calendar, entry,
ranking, club, and athlete data. The upstream sites are server-rendered HTML; fenceapi
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
curl "https://api.boutfence.com/v1/athletes/33233"
curl "https://api.boutfence.com/v1/athletes/33233?include=medals"
curl "https://api.boutfence.com/v1/athletes/33233?include=overview"
```

Interactive OpenAPI docs: `https://api.boutfence.com/docs`

From the browser or Boutfence:

```js
const events = await fetch("https://api.boutfence.com/v1/calendar?nation=GER")
  .then((r) => r.json());
```

Calendar, event, current, and athlete endpoints scrape Ophardt / Fencing Worldwide
**only on a cache miss**. Rankings and clubs are read from a local SQLite database
and are never scraped on a GET.

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

Self-hosters can change the cap in [`fenceapi.toml`](fenceapi.toml) (`[api] rate_limit`
and `rate_window`), or pass `python -m fenceapi serve --rate-limit 100`. Use
`--rate-limit 0` to disable it on a private machine.

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
| `GET` | `/v1/rankings/ger/epee/men/senior` | Stored ranking list (`?as_of=YYYY-MM-DD` for a snapshot) |
| `GET` | `/v1/athletes/33233` | Ophardt biography (cached ~1 hour) plus ranking history |
| `GET` | `/v1/clubs` | Club list |

CORS is open for `boutfence.com` and localhost. Add origins in `[api] cors` in
`fenceapi.toml`, or set `FENCEAPI_CORS`. Set `[api] api_key` (or `FENCEAPI_API_KEY`)
to require `X-API-Key` on data routes (`/docs` stays public).

### Athlete `include`

`GET /v1/athletes/{id}` returns the full biography by default (identity, medals,
results, match stats, season rankings, selections, memberships, and ranking
history). Pass `include` to keep only some sections. Identity fields (name,
nation, age, and so on) always stay. The page is still fetched once and cached;
`include` only trims the JSON.

```bash
curl "https://api.boutfence.com/v1/athletes/33233?include=medals"
curl "https://api.boutfence.com/v1/athletes/33233?include=overview"
curl "https://api.boutfence.com/v1/athletes/33233?include=medals,memberships"
```

| Value | What you get |
| --- | --- |
| `medals` | Medal counts |
| `exams` | Exams |
| `overview` | Medals + exams |
| `memberships` | Clubs |
| `selections` | National-team squads |
| `season_rankings` | Ranking-tab lists |
| `results` | Competition results |
| `match_stats` | Season win/loss stats |
| `profile` | Medals, exams, memberships, selections, season rankings |
| `history` | Stored ranking snapshots (`rankings` + `club_history`) |
| `rankings` | Stored ranking snapshots only |
| `club_history` | Club periods from ranking snapshots |

Same filter on the CLI: `python -m fenceapi athlete 33233 --include medals`.

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
python -m fenceapi serve
```

Local docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### Settings

Host, scrape timing, the daily window, and database paths live in
[`fenceapi.toml`](fenceapi.toml) in the working directory. Edit that file on the
server; you should not need to change systemd units for port, rate limit, or
scrape interval.

```toml
[scrape]
interval = 1.0    # CLI / API cache misses
jitter = 0.25

[api]
host = "127.0.0.1"
port = 8000
rate_limit = 100
rate_window = 60

[daily]
window = "06:00-22:00"
interval = 2.0    # bulk rankings refresh
jitter = 1.0
calendar = true
rankings = true
```

Optional `fenceapi.local.toml` in the same folder overlays those values and is
gitignored (use it for `api_key`). `--config PATH` or `FENCEAPI_CONFIG` selects a
file. CLI flags still override a single run.

```bash
python -m fenceapi settings          # print the resolved config
python -m fenceapi serve             # host/port/rate limit from [api]
python -m fenceapi daily-sync --dry-run
```

### systemd

Copy the units from `deploy/`, edit `WorkingDirectory` / `ExecStart` to your
checkout, then:

```bash
sudo cp deploy/fenceapi.service \
       deploy/fenceapi-sync.service deploy/fenceapi-sync.timer \
       deploy/fenceapi-daily.service deploy/fenceapi-daily.timer \
       /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fenceapi.service fenceapi-sync.timer fenceapi-daily.timer
```

`fenceapi.service` serves JSON using `[api]` (host, port, rate limit) and
`[scrape]` / `[paths]` from `fenceapi.toml`. `fenceapi-sync.timer` refreshes the
calendar cache about every 30 minutes (with a random extra delay so it does not
hit the same second). `fenceapi-daily.timer` starts shortly after midnight; the
job then **sleeps until a random hour** in `[daily] window` and refreshes the
calendar plus current-season rankings. A lock file prevents two scrapes from
overlapping.

Dry-run (prints the chosen time, does not scrape):

```bash
python -m fenceapi daily-sync --dry-run
```

Cron works the same way if you would rather not use systemd:

```cron
10 0 * * * cd /opt/fenceapi && .venv/bin/python -m fenceapi daily-sync >> /var/log/fenceapi-daily.log 2>&1
```

Put Caddy or nginx in front for HTTPS. Point a hostname at this machine (port
forward 443, or Tailscale / Cloudflare Tunnel if you do not want to expose the
origin IP). Keep the 1 second upstream scrape interval for the API — readers hit
the cache; only cache refreshes and the daily job talk to Ophardt.


### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `FENCEAPI_CONFIG` | `./fenceapi.toml` | Settings file (see above) |
| `FENCEAPI_RATE_LIMIT` | `[api] rate_limit` | Requests per IP per window (`0` disables) |
| `FENCEAPI_RATE_WINDOW` | `[api] rate_window` | Window length in seconds |
| `FENCEAPI_INTERVAL` | `[scrape] interval` | Seconds between upstream scrapes |
| `FENCEAPI_CORS` | `[api] cors` or Boutfence + localhost | Allowed browser origins |
| `FENCEAPI_API_KEY` | `[api] api_key` | Require `X-API-Key` on data routes |
| `FENCEAPI_EVENTS_DB` | `[paths] events_db` | Calendar / event cache |
| `FENCEAPI_RANKINGS_DB` | `[paths] rankings_db` | Rankings and clubs |

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

# Daily scrape (window / interval / jobs from fenceapi.toml)
python -m fenceapi settings
python -m fenceapi daily-sync
python -m fenceapi daily-sync --dry-run
python -m fenceapi daily-sync --now
python -m fenceapi daily-sync --window 08:00-20:00 --skip-calendar

# Rankings (federation × weapon × gender × age)
python -m fenceapi rankings
python -m fenceapi rankings ger
python -m fenceapi rankings ger epee men senior
python -m fenceapi rankings ger --all
python -m fenceapi rankings ger --season 2025 --all
python -m fenceapi rankings --all-regions --all-seasons
python -m fenceapi rankings --all-regions --refresh-current
python -m fenceapi rankings ger --refresh-current
python -m fenceapi rankings fie foil women u20
python -m fenceapi clubs
python -m fenceapi clubs -o data/clubs.json
python -m fenceapi athlete 33233
python -m fenceapi athlete 33233 --include medals
python -m fenceapi athlete 33233 --include overview
python -m fenceapi athlete 33233 --history-only

# HTTP API on this machine (host/port/rate limit from fenceapi.toml [api])
python -m fenceapi serve
python -m fenceapi serve --host 127.0.0.1 --port 8000
```

`--all` downloads every matching published ranking list. Current-season DFB often
only has a subset of ages published; use `--season 2025` for a fuller grid.

`--all-regions --all-seasons` stores each list once. `--refresh-current` re-fetches
the current season (or `--season`) and appends a snapshot only when the table
changed. Past copies stay queryable via `GET /v1/athletes/{id}` and
`?as_of=YYYY-MM-DD` on a ranking list. Clubs are rebuilt from the latest snapshot
only.

The older nation JSON widget is still available as
`python -m fenceapi calendar GER --json-widget`.

`python -m fenceapi daily-sync` reads `[daily]` from `fenceapi.toml`, waits until a
random time in `window`, then runs `sync-calendar` and
`rankings --all-regions --refresh-current`. `--now` skips the wait. CLI flags
override the file for that run.

`python -m fenceapi athlete 33233` fetches the Ophardt biography. Snapshot history
from `--db` is attached when present. `--include` uses the same values as the
API (`medals`, `overview`, `memberships`, …). `--history-only` skips the live
profile fetch.

### Python

```python
from fenceapi import Scraper

scraper = Scraper()
home = scraper.home()
tournament = scraper.tournament("33940-2026")
results = scraper.results("916515-2025")
profile = scraper.athlete(33233)
```

Requests to upstream sites are spaced **1 second** apart by default (`[scrape]` in
`fenceapi.toml`). Override with `--interval 0.5` or `HttpClient(min_interval=1.0)`.

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
| `fencing.ophardt.online/en/search/rankings` | Federation ranking catalogs |
| `/en/biography/athlete/{id}` | Athlete profile, results, rankings, selections, clubs |

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
tournament. The daily job is meant to run **once per day at a shifting hour** —
do not add a second cron that refreshes all rankings on a fixed clock.

## License

[MIT](LICENSE). Contributions and independent deployments are welcome.
