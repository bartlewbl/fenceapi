from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from fenceapi.event_store import EventStore
from fenceapi.rate_limit import (
    RateLimiter,
    client_ip,
    is_exempt,
    rate_limit_headers,
)
from fenceapi.scraper import Scraper
from fenceapi.service import ApiService, CacheMiss
from fenceapi.settings import Settings, load_settings, make_client
from fenceapi.store import RankingStore

DEFAULT_ORIGINS = [
    "https://boutfence.com",
    "https://www.boutfence.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app(
    service: ApiService | None = None,
    *,
    settings: Settings | None = None,
    rate_limit: int | None = None,
    rate_window: float | None = None,
) -> FastAPI:
    cfg = settings if settings is not None else load_settings()
    if service is None:
        service = service_from_settings(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "service", None) is None:
            app.state.service = service_from_settings(load_settings())
        yield

    limit = rate_limit if rate_limit is not None else _env_int("FENCEAPI_RATE_LIMIT", cfg.api.rate_limit)
    window = rate_window if rate_window is not None else _env_float("FENCEAPI_RATE_WINDOW", cfg.api.rate_window)
    limiter = RateLimiter(limit, window) if limit > 0 else None

    app = FastAPI(
        title="fenceapi",
        version="0.1.0",
        description=(
            "Open-source JSON API for fencing calendars, tournaments, athlete profiles, rankings, and clubs. "
            f"Public hosted instances allow {limit if limit > 0 else 'unlimited'} requests per minute per IP."
        ),
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.rate_limiter = limiter
    app.state.api_key = os.environ.get("FENCEAPI_API_KEY") or cfg.api.api_key

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        key = getattr(request.app.state, "api_key", None)
        public = request.url.path in {"/", "/docs", "/redoc", "/openapi.json", "/v1/health"}
        if key and not public and request.headers.get("x-api-key") != key:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.middleware("http")
    async def enforce_rate_limit(request: Request, call_next):
        current = getattr(request.app.state, "rate_limiter", None)
        if current is None or is_exempt(request.url.path):
            return await call_next(request)
        decision = current.check(client_ip(request))
        headers = rate_limit_headers(decision)
        if not decision.allowed:
            return JSONResponse(
                {
                    "error": "rate_limit_exceeded",
                    "limit": decision.limit,
                    "window_seconds": int(current.window_seconds),
                    "retry_after": decision.retry_after,
                },
                status_code=429,
                headers=headers,
            )
        response = await call_next(request)
        for name, value in headers.items():
            if name != "Retry-After":
                response.headers[name] = value
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(cfg),
        allow_origin_regex=r"https://.*\.boutfence\.com",
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/")
    def root() -> dict[str, Any]:
        current = getattr(app.state, "rate_limiter", None)
        payload: dict[str, Any] = {"ok": True, "docs": "/docs", "api": "/v1"}
        if current is not None:
            payload["rate_limit"] = {
                "requests": current.limit,
                "window_seconds": int(current.window_seconds),
            }
        return payload

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return _svc(app).health()

    @app.get("/v1/calendar")
    def calendar(
        nation: str | None = None,
        date_from: str | None = Query(None, alias="from"),
        date_to: str | None = Query(None, alias="to"),
        region: str | None = None,
        city: str | None = None,
        title: str | None = None,
        event_type: str | None = Query(None, alias="type"),
        weapon: str | None = None,
        gender: str | None = None,
        age: str | None = None,
        venue: str | None = "tournament",
        refresh: bool = False,
    ) -> dict[str, Any]:
        return _cached_call(
            _svc(app).calendar,
            refresh=refresh,
            nation=nation,
            date_from=date_from,
            date_to=date_to,
            region=region,
            city=city,
            title=title,
            event_type=event_type,
            weapon=weapon,
            gender=gender,
            age=age,
            venue=venue,
        )

    @app.get("/v1/events/{event_id}")
    def event(event_id: int, refresh: bool = False) -> dict[str, Any]:
        return _cached_call(_svc(app).event, event_id, refresh=refresh)

    @app.get("/v1/events/{event_id}/entries")
    def entries(event_id: int, refresh: bool = False) -> dict[str, Any]:
        return _cached_call(_svc(app).entries, event_id, refresh=refresh)

    @app.get("/v1/current")
    def current(nation: str | None = None, refresh: bool = False) -> dict[str, Any]:
        return _cached_call(_svc(app).current, nation=nation, refresh=refresh)

    @app.get("/v1/rankings")
    def ranking_federations() -> dict[str, Any]:
        items = _svc(app).ranking_federations()
        return {"count": len(items), "federations": items}

    @app.get("/v1/rankings/{federation}")
    def ranking_catalog(federation: str, season: int | None = None) -> dict[str, Any]:
        try:
            return _svc(app).ranking_catalog(federation, season=season)
        except (CacheMiss, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/rankings/{federation}/{weapon}/{gender}/{age}")
    def ranking_list(
        federation: str,
        weapon: str,
        gender: str,
        age: str,
        season: int | None = None,
        kind: str | None = None,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        try:
            return _svc(app).ranking_list(
                federation, weapon, gender, age, season=season, kind=kind, as_of=as_of
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CacheMiss as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/athletes/{athlete_id}")
    def athlete(
        athlete_id: int,
        refresh: bool = False,
        include: str | None = Query(
            None,
            description=(
                "Comma-separated sections to keep, e.g. medals or overview. "
                "Options: medals, exams, results, match_stats, season_rankings, "
                "selections, memberships, rankings, club_history; "
                "aliases: overview, profile, history."
            ),
        ),
    ) -> dict[str, Any]:
        try:
            return _svc(app).athlete(athlete_id, refresh=refresh, include=include)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CacheMiss as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/clubs")
    def clubs() -> dict[str, Any]:
        return _svc(app).clubs()

    return app


def _svc(app: FastAPI) -> ApiService:
    return app.state.service


def _cached_call(fn, *args, **kwargs) -> dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except CacheMiss as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _cors_origins(settings: Settings) -> list[str]:
    extra = [part.strip() for part in os.environ.get("FENCEAPI_CORS", "").split(",") if part.strip()]
    if extra:
        return extra
    if settings.api.cors:
        return list(settings.api.cors)
    return list(DEFAULT_ORIGINS)


def _env_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    return int(raw)


def _env_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return fallback
    return float(raw)


def service_from_settings(
    settings: Settings,
    *,
    interval: float | None = None,
    lang: str | None = None,
    rankings_db: str | None = None,
    events_db: str | None = None,
) -> ApiService:
    interval_env = os.environ.get("FENCEAPI_INTERVAL")
    if interval is None and interval_env not in (None, ""):
        interval = float(interval_env)
    return ApiService(
        scraper=Scraper(
            client=make_client(settings.scrape, interval=interval),
            lang=lang or settings.scrape.lang,
        ),
        events=EventStore(events_db or os.environ.get("FENCEAPI_EVENTS_DB") or settings.paths.events_db),
        rankings=RankingStore(
            rankings_db or os.environ.get("FENCEAPI_RANKINGS_DB") or settings.paths.rankings_db
        ),
        calendar_ttl=_env_int("FENCEAPI_CALENDAR_TTL", settings.api.calendar_ttl),
        event_ttl=_env_int("FENCEAPI_EVENT_TTL", settings.api.event_ttl),
        snapshot_ttl=_env_int("FENCEAPI_SNAPSHOT_TTL", settings.api.snapshot_ttl),
        athlete_ttl=_env_int("FENCEAPI_ATHLETE_TTL", settings.api.athlete_ttl),
    )


app = create_app()
