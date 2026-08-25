from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class DateRange:
    start: str | None = None
    end: str | None = None
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ResourceRef:
    """A fencingworldwide resource like 33940-2026."""

    id: int | None = None
    year: int | None = None
    slug: str = ""

    @property
    def key(self) -> str:
        if self.id is not None and self.year is not None:
            return f"{self.id}-{self.year}"
        if self.id is not None:
            return str(self.id)
        return self.slug

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "year": self.year, "key": self.key}


@dataclass(slots=True)
class TournamentSummary:
    title: str
    url: str
    status: str
    live_feed: bool = False
    date: DateRange = field(default_factory=DateRange)
    nation: str | None = None
    city: str | None = None
    weapons: list[str] = field(default_factory=list)
    genders: list[str] = field(default_factory=list)
    age_classes: list[str] = field(default_factory=list)
    categories_raw: str | None = None
    resource: ResourceRef = field(default_factory=ResourceRef)
    ophardt_event_url: str | None = None
    inscriptions_url: str | None = None
    external_url: str | None = None
    section: str = "current"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resource"] = self.resource.to_dict()
        data["date"] = self.date.to_dict()
        return data


@dataclass(slots=True)
class Competition:
    title: str
    url: str
    resource: ResourceRef = field(default_factory=ResourceRef)
    weapon: str | None = None
    gender: str | None = None
    age_class: str | None = None
    kind: str | None = None
    start_at: str | None = None
    status: str | None = None
    live_feed: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resource"] = self.resource.to_dict()
        return data


@dataclass(slots=True)
class Tournament:
    title: str
    url: str
    resource: ResourceRef
    subtitle: str | None = None
    nation: str | None = None
    city: str | None = None
    date: DateRange = field(default_factory=DateRange)
    competitions: list[Competition] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "url": self.url,
            "nation": self.nation,
            "city": self.city,
            "date": self.date.to_dict(),
            "resource": self.resource.to_dict(),
            "competitions": [item.to_dict() for item in self.competitions],
        }


@dataclass(slots=True)
class AthleteResult:
    rank: str | None
    rank_value: int | None
    tied: bool
    nation: str | None
    name: str
    athlete_id: int | None
    club: str | None = None
    region: str | None = None
    seed: str | None = None
    present: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CompetitionListing:
    resource: ResourceRef
    page: str
    url: str
    last_transmission: str | None = None
    transmitter: str | None = None
    rows: list[AthleteResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource.to_dict(),
            "page": self.page,
            "url": self.url,
            "last_transmission": self.last_transmission,
            "transmitter": self.transmitter,
            "rows": [item.to_dict() for item in self.rows],
        }


@dataclass(slots=True)
class TimetableEntry:
    date: str | None
    time: str | None
    competition: str | None
    phase: str | None
    table: str | None
    pistes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Timetable:
    resource: ResourceRef
    url: str
    last_transmission: str | None = None
    transmitter: str | None = None
    entries: list[TimetableEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource.to_dict(),
            "url": self.url,
            "last_transmission": self.last_transmission,
            "transmitter": self.transmitter,
            "entries": [item.to_dict() for item in self.entries],
        }


@dataclass(slots=True)
class HomePage:
    url: str
    nations: list[dict[str, str]] = field(default_factory=list)
    current: list[TournamentSummary] = field(default_factory=list)
    upcoming: list[TournamentSummary] = field(default_factory=list)
    recent: list[TournamentSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "nations": self.nations,
            "current": [item.to_dict() for item in self.current],
            "upcoming": [item.to_dict() for item in self.upcoming],
            "recent": [item.to_dict() for item in self.recent],
        }


@dataclass(slots=True)
class RankingFederation:
    id: int
    name: str
    level: str
    nation: str | None
    url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RankingCategory:
    ranking_id: int
    url: str
    group: str
    weapon: str
    gender: str
    age_class: str
    kind: str = "individual"
    label: str = ""

    @property
    def key(self) -> str:
        return f"{self.weapon}-{self.gender}-{self.age_class}-{self.kind}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["key"] = self.key
        return data


@dataclass(slots=True)
class RankingCatalog:
    federation: RankingFederation
    season: int | None
    title: str
    url: str
    categories: list[RankingCategory] = field(default_factory=list)
    seasons: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "federation": self.federation.to_dict(),
            "season": self.season,
            "seasons": self.seasons,
            "title": self.title,
            "url": self.url,
            "categories": [item.to_dict() for item in self.categories],
        }


@dataclass(slots=True)
class ClubMention:
    name: str
    region: str | None
    raw: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RankingEntry:
    rank: int | None
    points: float | None
    transferred_points: float | None
    name: str
    athlete_id: int | None
    nation: str | None
    clubs: str | None
    yob: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RankingList:
    ranking_id: int
    url: str
    title: str
    weapon: str | None
    gender: str | None
    age_class: str | None
    kind: str | None
    calculated_on: str | None
    season: int | None
    entries: list[RankingEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranking_id": self.ranking_id,
            "url": self.url,
            "title": self.title,
            "weapon": self.weapon,
            "gender": self.gender,
            "age_class": self.age_class,
            "kind": self.kind,
            "calculated_on": self.calculated_on,
            "season": self.season,
            "count": len(self.entries),
            "entries": [item.to_dict() for item in self.entries],
        }


@dataclass(slots=True)
class CalendarEvent:
    event_id: int
    url: str
    title: str
    subtitle: str | None
    date_start: str | None
    date_end: str | None
    nation: str | None
    region: str | None
    city: str | None
    age_classes: list[str] = field(default_factory=list)
    weapons: dict[str, list[str]] = field(default_factory=dict)
    invitation_url: str | None = None
    ics_url: str | None = None
    status: str | None = None
    updated: str | None = None
    open_for: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "url": self.url,
            "title": self.title,
            "subtitle": self.subtitle,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "nation": self.nation,
            "region": self.region,
            "city": self.city,
            "age_classes": self.age_classes,
            "weapons": self.weapons,
            "invitation_url": self.invitation_url,
            "ics_url": self.ics_url,
            "status": self.status,
            "updated": self.updated,
            "open_for": self.open_for,
        }


@dataclass(slots=True)
class EventCompetition:
    day: str | None
    weapon: str | None
    gender: str | None
    kind: str | None
    age_class: str | None
    birth_years: str | None
    master_competition_id: int | None
    entries_url: str | None
    medalists_url: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "weapon": self.weapon,
            "gender": self.gender,
            "kind": self.kind,
            "age_class": self.age_class,
            "birth_years": self.birth_years,
            "master_competition_id": self.master_competition_id,
            "entries_url": self.entries_url,
            "medalists_url": self.medalists_url,
        }


@dataclass(slots=True)
class RelatedEvent:
    event_id: int
    url: str
    dates: str | None
    location: str | None
    current: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "url": self.url,
            "dates": self.dates,
            "location": self.location,
            "current": self.current,
        }


@dataclass(slots=True)
class EventEntry:
    name: str
    year_of_birth: int | None
    nation: str | None
    club: str | None
    status: str | None
    seeding: str | None
    license_valid: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "year_of_birth": self.year_of_birth,
            "nation": self.nation,
            "club": self.club,
            "status": self.status,
            "seeding": self.seeding,
            "license_valid": self.license_valid,
        }


@dataclass(slots=True)
class EventCompetitionEntries:
    title: str
    competition_id: int | None
    entries: list[EventEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "competition_id": self.competition_id,
            "count": len(self.entries),
            "entries": [item.to_dict() for item in self.entries],
        }


@dataclass(slots=True)
class EventDetail:
    event_id: int
    url: str
    title: str
    subtitle: str | None
    date_start: str | None
    date_end: str | None
    nation: str | None
    region: str | None
    city: str | None
    invitation_url: str | None = None
    entries_url: str | None = None
    results_url: str | None = None
    live_results_url: str | None = None
    entries_open: str | None = None
    entries_close: str | None = None
    competitions: list[EventCompetition] = field(default_factory=list)
    related: list[RelatedEvent] = field(default_factory=list)
    inscriptions: list[EventCompetitionEntries] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "event_id": self.event_id,
            "url": self.url,
            "title": self.title,
            "subtitle": self.subtitle,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "nation": self.nation,
            "region": self.region,
            "city": self.city,
            "invitation_url": self.invitation_url,
            "entries_url": self.entries_url,
            "results_url": self.results_url,
            "live_results_url": self.live_results_url,
            "entries_open": self.entries_open,
            "entries_close": self.entries_close,
            "competitions": [item.to_dict() for item in self.competitions],
            "related": [item.to_dict() for item in self.related],
        }
        if self.inscriptions:
            payload["inscriptions"] = [item.to_dict() for item in self.inscriptions]
        return payload


@dataclass(slots=True)
class MedalCount:
    title: str
    gold: int
    silver: int
    bronze: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AthleteExam:
    date: str | None
    name: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AthleteCompetitionResult:
    rank: int | None
    date_start: str | None
    date_end: str | None
    city: str | None
    nation: str | None
    competition: str
    category: str
    competition_id: int | None = None
    url: str | None = None
    weapon: str | None = None
    gender: str | None = None
    age_class: str | None = None
    kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AthleteResultGroup:
    group: str
    results: list[AthleteCompetitionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "count": len(self.results),
            "results": [item.to_dict() for item in self.results],
        }


@dataclass(slots=True)
class AthleteSeasonRanking:
    rank: int | None
    points: float | None
    season: int | None
    title: str
    level: str
    category: str
    ranking_id: int | None = None
    url: str | None = None
    weapon: str | None = None
    gender: str | None = None
    age_class: str | None = None
    kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AthleteSelection:
    season: str
    selection: str
    weapon: str | None = None
    federation: str | None = None
    training_center: str | None = None
    coach: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AthleteMembership:
    club: str
    nation: str | None = None
    type: str | None = None
    start: str | None = None
    end: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AthleteProfile:
    athlete_id: int
    url: str
    name: str
    nation: str | None = None
    clubs: str | None = None
    weapons: list[str] = field(default_factory=list)
    age: int | None = None
    gender: str | None = None
    photo_url: str | None = None
    medals: list[MedalCount] = field(default_factory=list)
    exams: list[AthleteExam] = field(default_factory=list)
    results: list[AthleteResultGroup] = field(default_factory=list)
    match_stats: list[dict[str, Any]] = field(default_factory=list)
    season_rankings: list[AthleteSeasonRanking] = field(default_factory=list)
    selections: list[AthleteSelection] = field(default_factory=list)
    memberships: list[AthleteMembership] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "athlete_id": self.athlete_id,
            "url": self.url,
            "name": self.name,
            "nation": self.nation,
            "clubs": self.clubs,
            "weapons": self.weapons,
            "age": self.age,
            "gender": self.gender,
            "photo_url": self.photo_url,
            "medals": [item.to_dict() for item in self.medals],
            "exams": [item.to_dict() for item in self.exams],
            "results": [item.to_dict() for item in self.results],
            "match_stats": self.match_stats,
            "season_rankings": [item.to_dict() for item in self.season_rankings],
            "selections": [item.to_dict() for item in self.selections],
            "memberships": [item.to_dict() for item in self.memberships],
        }
