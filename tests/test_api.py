from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from fenceapi.api import create_app
from fenceapi.event_store import EventStore
from fenceapi.models import (
    CalendarEvent,
    EventDetail,
    HomePage,
    RankingCatalog,
    RankingCategory,
    RankingEntry,
    RankingFederation,
    RankingList,
)
from fenceapi.service import ApiService
from fenceapi.store import RankingStore


class FakeScraper:
    def __init__(self) -> None:
        self.calendar_calls = 0
        self.event_calls = 0

    def calendar(self, **kwargs):
        self.calendar_calls += 1
        return [
            CalendarEvent(
                event_id=34860,
                url="https://fencing.ophardt.online/en/widget/event/34860",
                title="2026 Erzgebirgs-Cup",
                subtitle=None,
                date_start="2026-08-22",
                date_end="2026-08-23",
                nation="GER",
                region="SN",
                city="Stollberg",
            )
        ]

    def event(self, event_id, include_entries: bool = False):
        self.event_calls += 1
        detail = EventDetail(
            event_id=int(event_id),
            url=f"https://fencing.ophardt.online/en/widget/event/{event_id}",
            title="2026 Erzgebirgs-Cup",
            subtitle=None,
            date_start="2026-08-22",
            date_end="2026-08-23",
            nation="GER",
            region="SN",
            city="Stollberg",
        )
        if include_entries:
            from fenceapi.models import EventCompetitionEntries, EventEntry

            detail.inscriptions = [
                EventCompetitionEntries(
                    title="Epee Women's U11 Individual",
                    competition_id=213701,
                    entries=[
                        EventEntry("DRUMMER Pauline", 2016, "GER", "FC Oelsnitz", "Inscribed", None, True)
                    ],
                )
            ]
        return detail

    def home(self, nation=None):
        return HomePage(url="https://www.fencingworldwide.com/en/")


def _app(
    tmp_path: Path,
    scraper: FakeScraper | None = None,
    *,
    rate_limit: int | None = None,
) -> tuple[TestClient, FakeScraper, ApiService]:
    scraper = scraper or FakeScraper()
    rankings = RankingStore(tmp_path / "rankings.sqlite")
    events = EventStore(tmp_path / "events.sqlite")
    _seed_rankings(rankings)
    service = ApiService(scraper, events, rankings, calendar_ttl=3600, event_ttl=3600)
    return TestClient(create_app(service, rate_limit=rate_limit)), scraper, service


def _seed_rankings(store: RankingStore) -> None:
    federation = RankingFederation(1, "Deutscher Fechter-Bund", "national", "GER", "https://example/1")
    catalog = RankingCatalog(
        federation=federation,
        season=2026,
        title="DFB",
        url=federation.url,
        categories=[
            RankingCategory(22576, "https://example/22576", "National", "epee", "men", "senior")
        ],
        seasons=[2026],
    )
    store.save_catalog(catalog)
    store.save_ranking(
        RankingList(
            ranking_id=22576,
            url="https://example/22576",
            title="Epee men senior",
            weapon="epee",
            gender="men",
            age_class="senior",
            kind="individual",
            calculated_on=None,
            season=2026,
            entries=[
                RankingEntry(1, 155.5, 0, "BELLMANN Lukas", 39083, "GER", "NW TSV Bayer 04 Leverkusen", 1995),
            ],
        ),
        federation=federation,
    )


def test_calendar_uses_cache(tmp_path: Path) -> None:
    client, scraper, _ = _app(tmp_path)
    first = client.get("/v1/calendar", params={"nation": "GER"})
    second = client.get("/v1/calendar", params={"nation": "GER"})
    assert first.status_code == 200
    assert first.json()["count"] == 1
    assert first.json()["events"][0]["event_id"] == 34860
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert scraper.calendar_calls == 1


def test_event_and_entries(tmp_path: Path) -> None:
    client, scraper, _ = _app(tmp_path)
    event = client.get("/v1/events/34860")
    assert event.status_code == 200
    assert event.json()["title"] == "2026 Erzgebirgs-Cup"
    entries = client.get("/v1/events/34860/entries")
    assert entries.json()["inscriptions"][0]["entries"][0]["club"] == "FC Oelsnitz"
    client.get("/v1/events/34860")
    assert scraper.event_calls == 2


def test_rankings_and_clubs_from_sqlite(tmp_path: Path) -> None:
    client, _, _ = _app(tmp_path)
    feds = client.get("/v1/rankings")
    assert feds.json()["count"] == 1
    catalog = client.get("/v1/rankings/ger")
    assert catalog.json()["season"] == 2026
    listing = client.get("/v1/rankings/ger/epee/men/senior")
    assert listing.json()["entries"][0]["name"] == "BELLMANN Lukas"
    clubs = client.get("/v1/clubs")
    assert clubs.json()["count"] >= 1


def test_missing_ranking_is_404(tmp_path: Path) -> None:
    client, _, _ = _app(tmp_path)
    response = client.get("/v1/rankings/ger/foil/women/u15")
    assert response.status_code == 404


def test_health(tmp_path: Path) -> None:
    client, _, _ = _app(tmp_path)
    assert client.get("/v1/health").json()["ok"] is True


def test_rate_limit_headers_and_429(tmp_path: Path) -> None:
    client, _, _ = _app(tmp_path, rate_limit=3)
    first = client.get("/v1/rankings")
    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "3"
    assert first.headers["x-ratelimit-remaining"] == "2"

    assert client.get("/v1/rankings").status_code == 200
    assert client.get("/v1/rankings").status_code == 200
    blocked = client.get("/v1/rankings")
    assert blocked.status_code == 429
    body = blocked.json()
    assert body["error"] == "rate_limit_exceeded"
    assert body["limit"] == 3
    assert blocked.headers["retry-after"]
    assert blocked.headers["x-ratelimit-remaining"] == "0"
    assert client.get("/v1/health").status_code == 200
    assert client.get("/").json()["rate_limit"]["requests"] == 3


def test_rate_limit_is_per_forwarded_ip(tmp_path: Path) -> None:
    client, _, _ = _app(tmp_path, rate_limit=1)
    assert client.get("/v1/rankings", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/v1/rankings", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert client.get("/v1/rankings", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200


def test_rate_limit_can_be_disabled(tmp_path: Path) -> None:
    client, _, _ = _app(tmp_path, rate_limit=0)
    for _ in range(5):
        assert client.get("/v1/rankings").status_code == 200
    assert "rate_limit" not in client.get("/").json()
