from __future__ import annotations

from pathlib import Path

import json
import sqlite3

import pytest

from fenceapi.models import (
    RankingCatalog,
    RankingCategory,
    RankingEntry,
    RankingFederation,
    RankingList,
)
from fenceapi.ranking_parsers import (
    filter_categories,
    parse_ranking_catalog,
    parse_ranking_federations,
    parse_ranking_list,
    resolve_federation,
)
from fenceapi.urls import ranking_show_url, rankings_federation_url

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_ranking_federations() -> None:
    feds = parse_ranking_federations(
        (FIXTURES / "rankings_index.html").read_text(encoding="utf-8"),
        "https://fencing.ophardt.online/en/search/rankings",
    )
    assert [item.id for item in feds] == [15, 1, 25]
    assert resolve_federation(feds, "fie").id == 15
    assert resolve_federation(feds, "ger").id == 1
    assert resolve_federation(feds, "GER").nation == "GER"
    assert resolve_federation(feds, "1").name.startswith("Deutscher")


def test_parse_ranking_catalog_and_filters() -> None:
    federation = RankingFederation(
        id=1,
        name="Deutscher Fechter-Bund e.V.",
        level="national",
        nation="GER",
        url="https://fencing.ophardt.online/en/search/rankings/1",
    )
    catalog = parse_ranking_catalog(
        (FIXTURES / "rankings_catalog.html").read_text(encoding="utf-8"),
        "https://fencing.ophardt.online/en/search/rankings/1",
        federation,
    )
    assert catalog.season == 2026
    assert catalog.seasons == [2026, 2025]
    assert len(catalog.categories) == 9
    senior_epee_men = filter_categories(
        catalog.categories, weapon="epee", gender="men", age="senior"
    )
    assert len(senior_epee_men) == 1
    assert senior_epee_men[0].ranking_id == 22576
    assert senior_epee_men[0].weapon == "epee"
    assert senior_epee_men[0].gender == "men"
    u15_foil = filter_categories(catalog.categories, weapon="foil", age="u15")
    assert u15_foil == []
    assert rankings_federation_url(1, 2025).endswith("/rankings/1?season=2025")


def test_parse_ranking_list() -> None:
    listing = parse_ranking_list(
        (FIXTURES / "ranking_list.html").read_text(encoding="utf-8"),
        ranking_show_url(22576),
        22576,
    )
    assert listing.weapon == "epee"
    assert listing.gender == "men"
    assert listing.age_class == "senior"
    assert listing.entries[0].name == "BELLMANN Lukas"
    assert listing.entries[0].athlete_id == 39083
    assert listing.entries[0].points == 155.5
    assert listing.entries[0].clubs == "TSV Bayer 04 Leverkusen"
    assert listing.entries[0].yob == 1995
    assert listing.entries[1].rank == 2


def test_parse_club_mentions() -> None:
    from fenceapi.ranking_parsers import parse_club_mentions

    clubs = parse_club_mentions("NW TSV Bayer 04 Leverkusen, (ZHR ZFC Zürich)")
    assert [(c.region, c.name) for c in clubs] == [
        ("ZHR", "ZFC Zürich"),
        ("NW", "TSV Bayer 04 Leverkusen"),
    ]
    assert parse_club_mentions("FC Berlin")[0].region is None
    assert parse_club_mentions("FC Berlin")[0].name == "FC Berlin"
    assert parse_club_mentions("SN Dresdner FC")[0].name == "Dresdner FC"
    assert parse_club_mentions("2007") == []
    assert parse_club_mentions("1995") == []


def test_store_extracts_clubs(tmp_path: Path) -> None:
    from fenceapi.models import RankingEntry, RankingFederation, RankingList
    from fenceapi.store import RankingStore

    store = RankingStore(tmp_path / "rankings.sqlite")
    federation = RankingFederation(1, "DFB", "national", "GER", "https://example")
    listing = RankingList(
        ranking_id=22576,
        url="https://example/22576",
        title="Deutsche Rangliste: 2026",
        weapon="epee",
        gender="men",
        age_class="senior",
        kind="individual",
        calculated_on=None,
        season=2026,
        entries=[
            RankingEntry(1, 155.5, 0, "BELLMANN Lukas", 39083, "GER", "NW TSV Bayer 04 Leverkusen, (ZHR ZFC Zürich)", 1995),
            RankingEntry(2, 10, 0, "Other", 2, "GER", "SN Dresdner FC", 2000),
        ],
    )
    store.save_ranking(listing, federation=federation)
    clubs = {item["name"]: item for item in store.list_clubs()}
    assert "TSV Bayer 04 Leverkusen" in clubs
    assert "ZFC Zürich" in clubs
    assert "Dresdner FC" in clubs
    assert "NW" in clubs["TSV Bayer 04 Leverkusen"]["regions"]
    exported = store.export_clubs(tmp_path / "clubs.json")
    payload = json.loads(exported.read_text(encoding="utf-8"))
    assert payload["count"] == 3
    assert store.stats()["snapshots"] == 1


def test_parse_calculated_on_and_as_of() -> None:
    from fenceapi.ranking_history import normalize_as_of, parse_calculated_on

    assert parse_calculated_on("24.07.2026. 11:59") == "2026-07-24T11:59:00"
    assert parse_calculated_on("2026-08-12") == "2026-08-12T00:00:00"
    assert normalize_as_of("2026-08-12") == "2026-08-12T23:59:59"
    from fenceapi.ranking_history import iso_key

    assert iso_key("2026-07-24T11:59:00") == "2026-07-24T11:59:00"
    assert iso_key("2026-08-21T16:03:14.316446+00:00") == "2026-08-21T16:03:14"
    assert iso_key("2026-07-24T11:59:00") <= iso_key(normalize_as_of("2026-07-24"))


def _sample_listing(**overrides: object) -> RankingList:
    listing = RankingList(
        ranking_id=22576,
        url="https://example/22576",
        title="Deutsche Rangliste: 2026",
        weapon="epee",
        gender="men",
        age_class="senior",
        kind="individual",
        calculated_on="24.07.2026. 11:59",
        season=2026,
        entries=[
            RankingEntry(1, 155.5, 0, "BELLMANN Lukas", 39083, "GER", "NW TSV Bayer 04 Leverkusen", 1995),
            RankingEntry(2, 10, 0, "Other", 2, "GER", "SN Dresdner FC", 2000),
        ],
    )
    for key, value in overrides.items():
        setattr(listing, key, value)
    return listing


def test_snapshots_preserve_ranking_history(tmp_path: Path) -> None:
    from fenceapi.store import RankingStore

    store = RankingStore(tmp_path / "rankings.sqlite")
    federation = RankingFederation(1, "DFB", "national", "GER", "https://example")
    first = store.save_ranking(_sample_listing(), federation=federation)
    assert first.created is True
    second = store.save_ranking(_sample_listing(), federation=federation)
    assert second.unchanged is True
    assert store.stats()["snapshots"] == 1

    updated = _sample_listing(
        calculated_on="12.08.2026. 10:00",
        entries=[
            RankingEntry(1, 160, 0, "BELLMANN Lukas", 39083, "GER", "FC Berlin", 1995),
            RankingEntry(3, 8, 0, "New", 9, "GER", "FC Leipzig", 2001),
        ],
    )
    change = store.save_ranking(updated, federation=federation)
    assert change.unchanged is False
    assert change.created is False
    assert [item["athlete_id"] for item in change.added] == [9]
    assert [item["athlete_id"] for item in change.removed] == [2]
    assert change.club_moves[0]["athlete_id"] == 39083
    assert change.club_moves[0]["from"] == "NW TSV Bayer 04 Leverkusen"
    assert store.stats()["snapshots"] == 2

    live = store.get_ranking(22576)
    assert live is not None
    assert live.entries[0].clubs == "FC Berlin"
    assert live.calculated_on == "12.08.2026. 10:00"

    past = store.get_ranking(22576, as_of="2026-07-24")
    assert past is not None
    assert past.entries[0].clubs == "NW TSV Bayer 04 Leverkusen"
    assert {item.athlete_id for item in past.entries} == {39083, 2}

    athlete = store.athlete_history(39083)
    assert athlete is not None
    assert athlete["name"] == "BELLMANN Lukas"
    assert len(athlete["rankings"]) == 2
    assert athlete["rankings"][0]["rank"] == 1
    assert athlete["rankings"][-1]["club"] == "FC Berlin"
    assert any(item["name"] == "FC Berlin" and item["to"] is None for item in athlete["clubs"])


def test_backfill_creates_snapshot_from_existing_lists(tmp_path: Path) -> None:
    from fenceapi.store import RankingStore

    db = tmp_path / "rankings.sqlite"
    store = RankingStore(db)
    store.save_ranking(_sample_listing())
    store._conn.execute("DELETE FROM ranking_entry_history")
    store._conn.execute("DELETE FROM ranking_snapshots")
    store._conn.commit()
    store.close()

    restored = RankingStore(db)
    assert restored.stats()["snapshots"] == 1
    assert restored.athlete_history(39083) is not None
    restored.close()


def test_refresh_current_skips_unchanged_lists(tmp_path: Path) -> None:
    from fenceapi.ranking_sync import RankingSyncer
    from fenceapi.store import RankingStore

    federation = RankingFederation(
        1, "Deutscher Fechter-Bund", "national", "GER", "https://example/1"
    )
    catalog = RankingCatalog(
        federation=federation,
        season=2026,
        title="DFB",
        url=federation.url,
        categories=[
            RankingCategory(22576, "https://example/22576", "National", "epee", "men", "senior")
        ],
        seasons=[2026, 2025],
    )
    listing = _sample_listing()

    class ScriptedScraper:
        def __init__(self) -> None:
            self.ranking_calls = 0

        def ranking_federations(self):
            return [federation]

        def ranking_catalog(self, fed, season=None):
            return catalog

        def ranking(self, ranking_id):
            self.ranking_calls += 1
            return listing

    store = RankingStore(tmp_path / "rankings.sqlite")
    scraper = ScriptedScraper()
    syncer = RankingSyncer(scraper, store, clubs_path=tmp_path / "clubs.json", progress=None)
    first = syncer.run(federations=[federation], all_seasons=False)
    assert first.lists_fetched == 1
    assert first.lists_skipped == 0
    assert scraper.ranking_calls == 1

    skipped = syncer.run(federations=[federation], all_seasons=False)
    assert skipped.lists_skipped == 1
    assert scraper.ranking_calls == 1

    refreshed = syncer.run(federations=[federation], all_seasons=False, refresh_current=True)
    assert refreshed.lists_unchanged == 1
    assert refreshed.lists_updated == 0
    assert scraper.ranking_calls == 2
    assert store.stats()["snapshots"] == 1


def test_failed_ranking_save_keeps_previous_data(tmp_path: Path) -> None:
    from fenceapi.store import RankingStore

    store = RankingStore(tmp_path / "rankings.sqlite")
    store.save_ranking(_sample_listing())

    def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    store._write_live_ranking = boom  # type: ignore[method-assign]
    with pytest.raises(sqlite3.OperationalError):
        store.save_ranking(
            _sample_listing(
                calculated_on="12.08.2026. 10:00",
                entries=[RankingEntry(1, 1, 0, "X", 1, "GER", "FC X", 2000)],
            )
        )
    live = store.get_ranking(22576)
    assert live is not None
    assert live.entries[0].athlete_id == 39083
    assert store.stats()["snapshots"] == 1


def test_empty_update_does_not_wipe_ranking(tmp_path: Path) -> None:
    from fenceapi.store import RankingStore

    store = RankingStore(tmp_path / "rankings.sqlite")
    store.save_ranking(_sample_listing())
    with pytest.raises(ValueError, match="empty list"):
        store.save_ranking(_sample_listing(calculated_on="12.08.2026. 10:00", entries=[]))
    live = store.get_ranking(22576)
    assert live is not None
    assert len(live.entries) == 2


def test_empty_catalog_keeps_stored_lists(tmp_path: Path) -> None:
    from fenceapi.store import RankingStore

    store = RankingStore(tmp_path / "rankings.sqlite")
    federation = RankingFederation(1, "DFB", "national", "GER", "https://example")
    store.save_catalog(
        RankingCatalog(
            federation=federation,
            season=2026,
            title="DFB",
            url=federation.url,
            categories=[
                RankingCategory(22576, "https://example/22576", "National", "epee", "men", "senior")
            ],
            seasons=[2026],
        )
    )
    store.save_catalog(
        RankingCatalog(
            federation=federation,
            season=2026,
            title="DFB",
            url=federation.url,
            categories=[],
            seasons=[2026],
        )
    )
    catalog = store.get_catalog("ger", 2026)
    assert catalog is not None
    assert catalog.categories[0].ranking_id == 22576


def test_failed_club_rebuild_keeps_clubs(tmp_path: Path) -> None:
    from fenceapi.store import RankingStore

    store = RankingStore(tmp_path / "rankings.sqlite")
    store.save_ranking(_sample_listing())
    names = {item["name"] for item in store.list_clubs()}

    def boom(_payload: object) -> None:
        store._conn.execute("DELETE FROM clubs")
        raise sqlite3.OperationalError("fail")

    store._write_clubs = boom  # type: ignore[method-assign]
    with pytest.raises(sqlite3.OperationalError):
        store.rebuild_clubs()
    assert {item["name"] for item in store.list_clubs()} == names


def test_reverted_ranking_records_a_new_snapshot(tmp_path: Path) -> None:
    from fenceapi.store import RankingStore

    store = RankingStore(tmp_path / "rankings.sqlite")
    first = _sample_listing()
    second = _sample_listing(
        calculated_on="12.08.2026. 10:00",
        entries=[
            RankingEntry(1, 160, 0, "BELLMANN Lukas", 39083, "GER", "FC Berlin", 1995),
        ],
    )
    store.save_ranking(first)
    store.save_ranking(second)
    store.save_ranking(_sample_listing(calculated_on="20.08.2026. 09:00"))
    assert store.stats()["snapshots"] == 3
    live = store.get_ranking(22576)
    assert live is not None
    assert live.entries[0].clubs == "NW TSV Bayer 04 Leverkusen"
