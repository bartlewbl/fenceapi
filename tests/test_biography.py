from __future__ import annotations

from pathlib import Path

from fenceapi.biography_parsers import parse_biography, parse_category_label, parse_us_dates
from fenceapi.urls import biography_url, parse_athlete_id

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_athlete_id_and_url() -> None:
    assert parse_athlete_id(39083) == 39083
    assert parse_athlete_id("39083") == 39083
    assert parse_athlete_id("https://fencing.ophardt.online/en/biography/athlete/39083") == 39083
    assert biography_url(39083).endswith("/en/biography/athlete/39083")


def test_parse_category_and_dates() -> None:
    assert parse_category_label("Epee Men's Senior Individual") == ("epee", "men", "senior", "individual")
    assert parse_category_label("Epee Men's Senior Team") == ("epee", "men", "senior", "team")
    assert parse_us_dates("7/22/26 / 7/30/26") == ("2026-07-22", "2026-07-30")
    assert parse_us_dates("5/14/22") == ("2022-05-14", "2022-05-14")


def test_parse_biography() -> None:
    profile = parse_biography(
        (FIXTURES / "biography.html").read_text(encoding="utf-8"),
        biography_url(39083),
        39083,
    )
    assert profile.name == "Lukas Bellmann"
    assert profile.nation == "GER"
    assert profile.age == 31
    assert profile.gender == "men"
    assert profile.weapons == ["epee"]
    assert profile.clubs == "(SEB Basel), TSV Bayer 04 Leverkusen"
    assert profile.photo_url.endswith("/images/athlete/F39083.jpg")
    assert profile.medals[0].title == "World Championships"
    assert profile.medals[0].gold == 1
    assert profile.medals[1].bronze == 7
    assert profile.exams[0].date == "2015-12-31"
    assert profile.exams[0].name.startswith("Turnierreifeprüfung")

    champs = profile.results[0]
    assert champs.group == "International Championships"
    first = champs.results[0]
    assert first.rank == 89
    assert first.date_start == "2026-07-22"
    assert first.date_end == "2026-07-30"
    assert first.city == "Hong Kong"
    assert first.nation == "HKG"
    assert first.competition == "World Championships"
    assert first.competition_id == 205713
    assert first.weapon == "epee"
    assert first.kind == "individual"

    zonal = profile.results[1]
    assert [item.rank for item in zonal.results] == [4, 33]
    assert zonal.results[0].city == "Genua"
    assert zonal.results[0].date_start == "2025-06-14"
    assert zonal.results[1].city == "Antony"

    assert profile.match_stats[0]["season"] == "2025/2026"
    assert profile.match_stats[0]["wins_round"] == 64
    assert profile.match_stats[1]["total_hits"] == 966

    assert profile.season_rankings[0].rank == 73
    assert profile.season_rankings[0].points == 23.5
    assert profile.season_rankings[0].ranking_id == 22601
    assert profile.season_rankings[0].level == "International"
    assert profile.season_rankings[1].title == "Deutsche Rangliste"

    assert profile.selections[0].selection == "Perspektivkader"
    assert profile.selections[0].federation.startswith("Deutscher")
    assert [item.club for item in profile.memberships] == [
        "SEB Basel",
        "TSV Bayer 04 Leverkusen",
        "ZFC Zürich",
    ]
    assert profile.memberships[0].nation == "SUI"
    assert profile.memberships[0].start == "2021-05-20"
    assert profile.memberships[1].note == "First membership"
    assert profile.memberships[2].end == "2023-12-31"
