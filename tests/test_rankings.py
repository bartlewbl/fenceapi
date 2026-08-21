from __future__ import annotations

from pathlib import Path

import json

from fenceapi.models import RankingFederation
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
