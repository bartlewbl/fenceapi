from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from fenceapi.client import HttpClient
from fenceapi.event_store import EventStore
from fenceapi.rate_limit import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_RATE_WINDOW,
    RateLimiter,
    client_ip,
    is_exempt,
    rate_limit_headers,
)
from fenceapi.scraper import Scraper
from fenceapi.service import ApiService, CacheMiss
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
    rate_limit: int | None = None,
    rate_window: float | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "service", None) is None:
            app.state.service = _default_service()
        yield

    limit = _env_rate_limit() if rate_limit is None else rate_limit
    window = _env_rate_window() if rate_window is None else rate_window
    limiter = RateLimiter(limit, window) if limit > 0 else None

    app = FastAPI(
        title="fenceapi",
        version="0.1.0",
        description=(
            "Open-source JSON API for fencing calendars, tournaments, rankings, and clubs. "
            f"Public hosted instances allow {DEFAULT_RATE_LIMIT} requests per minute per IP."
        ),
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.rate_limiter = limiter

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        key = os.environ.get("FENCEAPI_API_KEY")
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
        allow_origins=_cors_origins(),
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
    ) -> dict[str, Any]:
        try:
            return _svc(app).ranking_list(
                federation, weapon, gender, age, season=season, kind=kind
            )
        except (CacheMiss, ValueError) as exc:
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


def _cors_origins() -> list[str]:
    extra = [part.strip() for part in os.environ.get("FENCEAPI_CORS", "").split(",") if part.strip()]
    return extra or DEFAULT_ORIGINS


def _env_rate_limit() -> int:
    raw = os.environ.get("FENCEAPI_RATE_LIMIT")
    if raw is None or raw == "":
        return DEFAULT_RATE_LIMIT
    return int(raw)


def _env_rate_window() -> float:
    raw = os.environ.get("FENCEAPI_RATE_WINDOW")
    if raw is None or raw == "":
        return DEFAULT_RATE_WINDOW
    return float(raw)


def _default_service() -> ApiService:
    interval = float(os.environ.get("FENCEAPI_INTERVAL", "1.0"))
    return ApiService(
        scraper=Scraper(client=HttpClient(min_interval=interval)),
        events=EventStore(os.environ.get("FENCEAPI_EVENTS_DB", "data/events.sqlite")),
        rankings=RankingStore(os.environ.get("FENCEAPI_RANKINGS_DB", "data/rankings.sqlite")),
        calendar_ttl=int(os.environ.get("FENCEAPI_CALENDAR_TTL", str(30 * 60))),
        event_ttl=int(os.environ.get("FENCEAPI_EVENT_TTL", str(15 * 60))),
    )


app = create_app()
