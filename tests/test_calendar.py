from __future__ import annotations

from pathlib import Path

from fenceapi.calendar_parsers import parse_calendar, parse_event, parse_inscriptions
from fenceapi.urls import ophardt_calendar_url, parse_event_id

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_calendar() -> None:
    events = parse_calendar(
        (FIXTURES / "calendar.html").read_text(encoding="utf-8"),
        "https://fencing.ophardt.online/en/calendar",
    )
    assert [item.event_id for item in events] == [34472, 34860]
    first = events[0]
    assert first.title == "Pan American Youth and Veterans Championships"
    assert first.subtitle == "CPE"
    assert first.date_start == "2026-08-16"
    assert first.date_end == "2026-08-21"
    assert first.nation == "PER"
    assert first.city == "Lima"
    assert first.weapons["epee"] == ["men", "women"]
    second = events[1]
    assert second.title == "2026 Erzgebirgs-Cup"
    assert second.nation == "GER"
    assert second.region == "SN"
    assert second.city == "Stollberg"
    assert "foil" not in second.weapons
    assert second.age_classes[0] == "Senior"


def test_parse_event() -> None:
    event = parse_event(
        (FIXTURES / "event.html").read_text(encoding="utf-8"),
        "https://fencing.ophardt.online/en/widget/event/34860",
        34860,
    )
    assert event.title == "2026 Erzgebirgs-Cup"
    assert event.date_start == "2026-08-22"
    assert event.nation == "GER"
    assert event.region == "SN"
    assert event.city == "Stollberg"
    assert event.entries_open == "2026-07-24"
    assert event.entries_close == "2026-08-21"
    assert event.live_results_url.endswith("/34860-2026/tournament/")
    assert len(event.competitions) == 2
    first, second = event.competitions
    assert first.day == "22.08."
    assert first.weapon == "epee"
    assert first.gender == "women"
    assert first.age_class == "Senior"
    assert first.master_competition_id == 909097
    assert second.day == "22.08."
    assert second.gender == "men"
    assert event.related[0].current is True
    assert event.related[1].event_id == 33127
    assert event.related[1].location == "GER SN Aue"


def test_parse_inscriptions() -> None:
    groups = parse_inscriptions(
        (FIXTURES / "inscriptions.html").read_text(encoding="utf-8"),
        "https://fencing.ophardt.online/en/inscriptions/show/34860",
    )
    assert [item.title for item in groups] == [
        "Epee Women's U11 Individual",
        "Epee Men's Senior Individual",
    ]
    assert groups[0].competition_id == 213701
    first = groups[0].entries[0]
    assert first.name == "DRUMMER Pauline"
    assert first.year_of_birth == 2016
    assert first.nation == "GER"
    assert first.club == "FC Oelsnitz"
    assert first.status == "Inscribed"
    assert first.license_valid is True
    assert groups[1].entries[0].seeding == "1"


def test_calendar_urls() -> None:
    url = ophardt_calendar_url(nation="GER", date_from="2026-08-01", discipline="E")
    assert "nation=GER" in url
    assert "date-from=2026-08-01" in url
    assert parse_event_id("/en/widget/event/34860/15") == 34860
    assert parse_event_id("34860") == 34860
