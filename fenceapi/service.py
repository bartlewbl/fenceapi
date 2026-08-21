from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fenceapi.calendar_query import calendar_filters
from fenceapi.event_store import EventStore
from fenceapi.ranking_parsers import filter_categories
from fenceapi.scraper import Scraper
from fenceapi.store import RankingStore

CALENDAR_TTL = 30 * 60
EVENT_TTL = 15 * 60
SNAPSHOT_TTL = 10 * 60


class CacheMiss(Exception):
    """No cached payload and a live fetch is not allowed or failed."""


class ApiService:
    def __init__(
        self,
        scraper: Scraper,
        events: EventStore,
        rankings: RankingStore,
        calendar_ttl: int = CALENDAR_TTL,
        event_ttl: int = EVENT_TTL,
        snapshot_ttl: int = SNAPSHOT_TTL,
    ) -> None:
        self.scraper = scraper
        self.events = events
        self.rankings = rankings
        self.calendar_ttl = calendar_ttl
        self.event_ttl = event_ttl
        self.snapshot_ttl = snapshot_ttl

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "events": self.events.stats(),
            "rankings": self.rankings.stats(),
        }

    def calendar(self, refresh: bool = False, **filters: str | None) -> dict[str, Any]:
        params = calendar_filters(**filters)
        key = _cache_key(params)
        return self._cached(
            "calendar",
            key,
            self.calendar_ttl,
            refresh=refresh,
            fetch=lambda: self._fetch_calendar(params),
        )

    def event(self, event_id: int, refresh: bool = False) -> dict[str, Any]:
        return self._cached(
            "event",
            str(event_id),
            self.event_ttl,
            refresh=refresh,
            fetch=lambda: self.scraper.event(event_id).to_dict(),
        )

    def entries(self, event_id: int, refresh: bool = False) -> dict[str, Any]:
        return self._cached(
            "entries",
            str(event_id),
            self.event_ttl,
            refresh=refresh,
            fetch=lambda: self._fetch_entries(event_id),
        )

    def current(self, nation: str | None = None, refresh: bool = False) -> dict[str, Any]:
        key = (nation or "all").lower()
        return self._cached(
            "current",
            key,
            self.snapshot_ttl,
            refresh=refresh,
            fetch=lambda: self.scraper.home(nation=nation).to_dict(),
        )

    def ranking_federations(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.rankings.list_federations()]

    def ranking_catalog(self, federation: str, season: int | None = None) -> dict[str, Any]:
        catalog = self.rankings.get_catalog(federation, season=season)
        if catalog is None:
            raise CacheMiss(
                "No ranking catalog in the local database. "
                "Run: python -m fenceapi rankings --all-regions --all-seasons"
            )
        return catalog.to_dict()

    def ranking_list(
        self,
        federation: str,
        weapon: str,
        gender: str,
        age: str,
        season: int | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        catalog = self.rankings.get_catalog(federation, season=season)
        if catalog is None:
            raise CacheMiss("No ranking catalog in the local database.")
        matches = filter_categories(
            catalog.categories, weapon=weapon, gender=gender, age=age, kind=kind
        )
        if not matches:
            raise CacheMiss("No published list matches those filters in the local database.")
        ranking = self.rankings.get_ranking(matches[0].ranking_id)
        if ranking is None:
            raise CacheMiss(
                f"Catalog points at ranking {matches[0].ranking_id} but the list is not stored yet."
            )
        payload = ranking.to_dict()
        payload["group"] = matches[0].group
        payload["key"] = matches[0].key
        payload["federation"] = catalog.federation.to_dict()
        return payload

    def clubs(self) -> dict[str, Any]:
        clubs = self.rankings.list_clubs()
        return {"count": len(clubs), "clubs": clubs}

    def sync_calendar(
        self,
        nation: str | None = None,
        details: bool = False,
        entries: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        payload = self.calendar(refresh=True, nation=nation)
        events = payload.get("events") or []
        if limit is not None:
            events = events[:limit]
        expanded = 0
        if details or entries:
            for item in events:
                event_id = item.get("event_id")
                if not event_id:
                    continue
                self.event(int(event_id), refresh=True)
                if entries:
                    self.entries(int(event_id), refresh=True)
                expanded += 1
        self.current(nation=nation.lower() if nation else None, refresh=True)
        return {
            "ok": True,
            "calendar_count": payload.get("count"),
            "details": expanded,
            "fetched_at": payload.get("fetched_at"),
        }

    def _fetch_calendar(self, params: dict[str, str | None]) -> dict[str, Any]:
        events = self.scraper.calendar(
            nation=params.get("nation"),
            date_from=params.get("date_from"),
            date_to=params.get("date_to"),
            region=params.get("region"),
            city=params.get("city"),
            title=params.get("title"),
            group=params.get("group"),
            discipline=params.get("discipline"),
            gender=params.get("gender"),
            ageclass=params.get("ageclass"),
            venuetype=params.get("venuetype"),
        )
        return {"count": len(events), "events": [item.to_dict() for item in events]}

    def _fetch_entries(self, event_id: int) -> dict[str, Any]:
        detail = self.scraper.event(event_id, include_entries=True)
        event_payload = detail.to_dict()
        self.events.put("event", str(event_id), {k: v for k, v in event_payload.items() if k != "inscriptions"})
        return {
            "event_id": event_id,
            "inscriptions": event_payload.get("inscriptions") or [],
        }

    def _cached(
        self,
        kind: str,
        cache_key: str,
        ttl: int,
        fetch,
        refresh: bool = False,
    ) -> dict[str, Any]:
        stored = None if refresh else self.events.get(kind, cache_key)
        if stored is not None and not _expired(stored[1], ttl):
            return _wrap(stored[0], fetched_at=stored[1], cached=True)
        try:
            payload = fetch()
            fetched_at = self.events.put(kind, cache_key, payload)
            return _wrap(payload, fetched_at=fetched_at, cached=False)
        except Exception as exc:
            stored = stored or self.events.get(kind, cache_key)
            if stored is not None:
                return _wrap(stored[0], fetched_at=stored[1], cached=True, stale=True)
            raise CacheMiss(str(exc)) from exc


def _wrap(
    payload: Any,
    fetched_at: str,
    cached: bool,
    stale: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {"data": payload}
    return {
        **payload,
        "fetched_at": fetched_at,
        "cached": cached,
        "stale": stale,
    }


def _cache_key(params: dict[str, str | None]) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _expired(fetched_at: str, ttl: int) -> bool:
    if ttl <= 0:
        return True
    try:
        stamp = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return age > ttl
