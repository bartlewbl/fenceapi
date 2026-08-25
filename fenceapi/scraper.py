from __future__ import annotations

from typing import Any

from fenceapi.client import HttpClient
from fenceapi.biography_parsers import parse_biography
from fenceapi.calendar_parsers import parse_calendar, parse_event, parse_inscriptions
from fenceapi.models import (
    AthleteProfile,
    CalendarEvent,
    CompetitionListing,
    EventDetail,
    HomePage,
    RankingCatalog,
    RankingFederation,
    RankingList,
    Timetable,
    Tournament,
    TournamentSummary,
)
from fenceapi.parsers import (
    parse_archive,
    parse_home,
    parse_participants,
    parse_results,
    parse_timetable,
    parse_tournament,
)
from fenceapi.ranking_parsers import (
    filter_categories,
    parse_ranking_catalog,
    parse_ranking_federations,
    parse_ranking_list,
    resolve_federation,
)
from fenceapi.urls import (
    archive_url,
    biography_url,
    home_url,
    ophardt_calendar_json_url,
    ophardt_calendar_url,
    ophardt_entries_url,
    ophardt_event_url,
    parse_athlete_id,
    parse_event_id,
    parse_resource_key,
    ranking_show_url,
    rankings_federation_url,
    rankings_index_url,
    resource_url,
)


class Scraper:
    def __init__(self, client: HttpClient | None = None, lang: str = "en") -> None:
        self.client = client or HttpClient()
        self.lang = lang
        self._federations: list[RankingFederation] | None = None

    def home(self, nation: str | None = None) -> HomePage:
        url = home_url(self.lang, nation)
        return parse_home(self.client.get_text(url), url)

    def archive(self, year: int) -> list[TournamentSummary]:
        url = archive_url(year, self.lang)
        return parse_archive(self.client.get_text(url), url)

    def tournament(self, resource_key: str) -> Tournament:
        url = resource_url(resource_key, "tournament", self.lang)
        return parse_tournament(self.client.get_text(url), url)

    def results(self, resource_key: str) -> CompetitionListing:
        url = resource_url(resource_key, "results", self.lang)
        return parse_results(self.client.get_text(url), url)

    def participants(self, resource_key: str) -> CompetitionListing:
        url = resource_url(resource_key, "participants", self.lang)
        return parse_participants(self.client.get_text(url), url)

    def timetable(self, resource_key: str) -> Timetable:
        url = resource_url(resource_key, "timetable", self.lang)
        return parse_timetable(self.client.get_text(url), url)

    def ophardt_calendar(self, nation: str) -> Any:
        """Official Ophardt calendar JSON widget (not fencingworldwide HTML)."""
        return self.client.get_json(ophardt_calendar_json_url(nation, self.lang))

    def calendar(
        self,
        nation: str | None = None,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        region: str | None = None,
        city: str | None = None,
        title: str | None = None,
        group: str | None = None,
        discipline: str | None = None,
        gender: str | None = None,
        ageclass: str | None = None,
        venuetype: str | None = "T",
    ) -> list[CalendarEvent]:
        url = ophardt_calendar_url(
            self.lang,
            date_from=date_from,
            date_to=date_to,
            nation=nation,
            region=region,
            city=city,
            title=title,
            group=group,
            discipline=discipline,
            gender=gender,
            ageclass=ageclass,
            venuetype=venuetype,
        )
        return parse_calendar(self.client.get_text(url), url)

    def event(self, event_id: str | int, include_entries: bool = False) -> EventDetail:
        resolved = parse_event_id(event_id)
        if resolved is None:
            raise ValueError(f"Invalid event id '{event_id}'.")
        url = ophardt_event_url(resolved, self.lang)
        detail = parse_event(self.client.get_text(url), url, resolved)
        if include_entries:
            entries_url = ophardt_entries_url(resolved, self.lang)
            detail.inscriptions = parse_inscriptions(
                self.client.get_text(entries_url),
                entries_url,
            )
        return detail

    def ranking_federations(self) -> list[RankingFederation]:
        if self._federations is None:
            url = rankings_index_url(self.lang)
            self._federations = parse_ranking_federations(self.client.get_text(url), url)
        return self._federations

    def ranking_catalog(
        self,
        federation: str | int | RankingFederation,
        season: int | None = None,
    ) -> RankingCatalog:
        resolved = self._federation(federation)
        url = rankings_federation_url(resolved.id, season, self.lang)
        return parse_ranking_catalog(self.client.get_text(url), url, resolved)

    def ranking(self, ranking_id: int) -> RankingList:
        url = ranking_show_url(ranking_id, self.lang)
        return parse_ranking_list(self.client.get_text(url), url, ranking_id)

    def athlete(self, athlete_id: str | int) -> AthleteProfile:
        resolved = parse_athlete_id(athlete_id)
        if resolved is None:
            raise ValueError(f"Invalid athlete id '{athlete_id}'.")
        url = biography_url(resolved, self.lang)
        return parse_biography(self.client.get_text(url), url, resolved)

    def rankings(
        self,
        federation: str | int,
        weapon: str | None = None,
        gender: str | None = None,
        age: str | None = None,
        kind: str | None = None,
        group: str | None = None,
        season: int | None = None,
        fetch_lists: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        catalog = self.ranking_catalog(federation, season=season)
        categories = filter_categories(
            catalog.categories,
            weapon=weapon,
            gender=gender,
            age=age,
            kind=kind,
            group=group,
        )
        payload: dict[str, Any] = {
            **catalog.to_dict(),
            "categories": [item.to_dict() for item in categories],
            "matched": len(categories),
        }
        if not fetch_lists:
            return payload
        lists: list[dict[str, Any]] = []
        for category in categories:
            ranking = self.ranking(category.ranking_id)
            data = ranking.to_dict()
            data["group"] = category.group
            data["key"] = category.key
            if limit is not None:
                data["entries"] = data["entries"][:limit]
                data["count"] = len(data["entries"])
            lists.append(data)
        payload["lists"] = lists
        return payload

    def _federation(self, federation: str | int | RankingFederation) -> RankingFederation:
        if isinstance(federation, RankingFederation):
            return federation
        return resolve_federation(self.ranking_federations(), str(federation))

    def snapshot(
        self,
        nation: str | None = None,
        include_competitions: bool = True,
        include_results: bool = False,
    ) -> dict[str, Any]:
        home = self.home(nation)
        payload: dict[str, Any] = home.to_dict()
        if not include_competitions:
            return payload

        details: list[dict[str, Any]] = []
        seen: set[str] = set()
        for summary in home.current + home.upcoming:
            key = summary.resource.key
            if not key or key in seen or summary.status == "external":
                continue
            seen.add(key)
            try:
                parse_resource_key(key)
            except ValueError:
                continue
            tournament = self.tournament(key)
            item = tournament.to_dict()
            if include_results:
                item["results"] = []
                for competition in tournament.competitions:
                    if not competition.resource.key:
                        continue
                    listing = self.results(competition.resource.key)
                    item["results"].append(listing.to_dict())
            details.append(item)
        payload["details"] = details
        return payload
