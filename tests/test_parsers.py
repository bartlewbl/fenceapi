from __future__ import annotations

from pathlib import Path

from fenceapi.parsers import (
    parse_archive,
    parse_categories,
    parse_date_range,
    parse_home,
    parse_participants,
    parse_results,
    parse_timetable,
    parse_tournament,
)
from fenceapi.urls import parse_resource, parse_resource_key, resource_url

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_home() -> None:
    home = parse_home(_load("home.html"), "https://www.fencingworldwide.com/en/")
    assert [n["code"] for n in home.nations] == ["ALL", "GER", "BRA"]
    assert len(home.current) == 2
    first = home.current[0]
    assert first.title.startswith("Torneio")
    assert first.nation == "BRA"
    assert first.city == "Belo Horizonte"
    assert first.status == "transferred"
    assert first.resource.key == "33940-2026"
    assert first.date.start == "2026-08-22"
    assert first.weapons == ["Epee", "Sabre"]
    assert first.genders == ["Men's", "Women's"]
    assert first.age_classes == ["Senior"]
    external = home.current[1]
    assert external.status == "external"
    assert external.resource.id == 34472
    assert external.date.start.endswith("-08-16")
    assert home.upcoming[0].title == "Campeonato Paulista"
    assert home.recent[0].status == "archive"


def test_parse_tournament() -> None:
    tournament = parse_tournament(
        _load("tournament.html"),
        "https://www.fencingworldwide.com/en/33940-2026/tournament/",
    )
    assert tournament.title.startswith("Torneio")
    assert tournament.nation == "BRA"
    assert tournament.city == "Belo Horizonte"
    assert [c.resource.id for c in tournament.competitions] == [14707, 14705, 14706]
    epee_men = tournament.competitions[0]
    assert epee_men.weapon == "Epee"
    assert epee_men.gender == "men"
    assert epee_men.age_class == "Senior"
    assert epee_men.start_at == "22.08. 09:00"
    assert epee_men.status == "current"
    assert tournament.competitions[2].weapon == "Sabre"


def test_parse_results() -> None:
    listing = parse_results(
        _load("results.html"),
        "https://www.fencingworldwide.com/en/916515-2025/results/",
    )
    assert listing.last_transmission == "11.01.2026 16:01"
    assert listing.transmitter == "Ralph Orschel"
    assert listing.rows[0].rank_value == 1
    assert listing.rows[0].nation == "POL"
    assert listing.rows[0].athlete_id == 409923
    assert listing.rows[1].tied is True
    assert listing.rows[1].rank_value == 3


def test_parse_participants() -> None:
    listing = parse_participants(
        _load("participants.html"),
        "https://www.fencingworldwide.com/en/916515-2025/participants/",
    )
    row = listing.rows[0]
    assert row.name == "AHAUS Viktoria"
    assert row.seed == "4.99999"
    assert row.present is True


def test_parse_timetable() -> None:
    table = parse_timetable(
        _load("timetable.html"),
        "https://www.fencingworldwide.com/en/916515-2025/timetable/",
    )
    assert table.entries[0].phase == "First round"
    assert table.entries[0].pistes == ["13", "14", "15"]
    assert table.entries[1].table == "T128"


def test_parse_archive() -> None:
    items = parse_archive(
        _load("archive.html"),
        "https://www.fencingworldwide.com/en/archive/2026",
    )
    assert items[0].title == "Weißer Bär von Berlin"
    assert items[0].date.start == "2026-01-10"
    assert items[0].live_feed is True
    assert items[0].nation == "GER"


def test_parse_categories_and_dates() -> None:
    weapons, genders, ages = parse_categories("Foil | Women's, Men's - U17, U15")
    assert weapons == ["Foil"]
    assert genders == ["Women's", "Men's"]
    assert ages == ["U17", "U15"]
    dates = parse_date_range("22.08. - 23.08.", fallback_year=2026)
    assert dates.start == "2026-08-22"
    assert dates.end == "2026-08-23"


def test_resource_urls() -> None:
    assert parse_resource_key("33940-2026") == (33940, 2026)
    assert parse_resource("/en/33940-2026/tournament/") == (33940, 2026, "tournament")
    assert resource_url("916515-2025", "results") == (
        "https://www.fencingworldwide.com/en/916515-2025/results/"
    )
