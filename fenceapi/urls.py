from __future__ import annotations

import re
from urllib.parse import urlencode, urljoin

BASE_URL = "https://www.fencingworldwide.com"
OPHARDT_BASE = "https://fencing.ophardt.online"
DEFAULT_LANG = "en"

RESOURCE_RE = re.compile(
    r"/(?P<lang>[a-z]{2})/(?P<id>\d+)-(?P<year>20\d{2})/(?P<page>[a-z0-9-]+)(?:/|$)",
    re.I,
)
ATHLETE_RE = re.compile(r"/athlete/(\d+)/")
PARTICIPANT_RE = re.compile(r"/participant/(\d+)")
WIDGET_EVENT_RE = re.compile(r"(?:fencing\.ophardt\.online)?/[^/]+/widget/event/(\d+)")
EVENT_ID_RE = re.compile(r"/widget/event/(\d+)")
INSCRIPTIONS_RE = re.compile(r"fencing\.ophardt\.online/[^/]+/inscriptions/show/(\d+)")
NATION_PATH_RE = re.compile(r"^/[a-z]{2}/([a-z]{3})$", re.I)


def absolute(path_or_url: str, base: str = BASE_URL) -> str:
    return urljoin(base.rstrip("/") + "/", path_or_url)


def home_url(lang: str = DEFAULT_LANG, nation: str | None = None) -> str:
    if nation:
        return f"{BASE_URL}/{lang}/{nation.lower()}"
    return f"{BASE_URL}/{lang}/"


def archive_url(year: int, lang: str = DEFAULT_LANG) -> str:
    return f"{BASE_URL}/{lang}/archive/{year}"


def resource_url(resource_key: str, page: str, lang: str = DEFAULT_LANG) -> str:
    page = page.strip("/")
    return f"{BASE_URL}/{lang}/{resource_key}/{page}/"


def parse_resource(url: str) -> tuple[int, int, str] | None:
    match = RESOURCE_RE.search(url)
    if not match:
        return None
    return int(match.group("id")), int(match.group("year")), match.group("page")


def parse_resource_key(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)-(20\d{2})", value.strip())
    if match:
        return int(match.group(1)), int(match.group(2))
    if value.isdigit():
        raise ValueError(
            f"Need a season year, e.g. '{value}-2026'. Got '{value}'."
        )
    raise ValueError(f"Invalid resource key '{value}'. Expected id-year, e.g. 33940-2026.")


def ophardt_calendar_json_url(nation: str, lang: str = DEFAULT_LANG) -> str:
    return f"{OPHARDT_BASE}/{lang}/widget/calendar-json/{nation.upper()}"


def ophardt_calendar_url(
    lang: str = DEFAULT_LANG,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    nation: str | None = None,
    region: str | None = None,
    city: str | None = None,
    title: str | None = None,
    group: str | None = None,
    discipline: str | None = None,
    gender: str | None = None,
    ageclass: str | None = None,
    venuetype: str | None = "T",
) -> str:
    params: dict[str, str] = {}
    if date_from:
        params["date-from"] = date_from
    if date_to:
        params["date-to"] = date_to
    if nation:
        params["nation"] = nation.upper()
    if region:
        params["region"] = region
    if city:
        params["city"] = city
    if title:
        params["title"] = title
    if group:
        params["group"] = group
    if discipline:
        params["discipline"] = discipline
    if gender:
        params["gender"] = gender
    if ageclass:
        params["ageclass"] = ageclass
    if venuetype:
        params["venuetype"] = venuetype
    url = f"{OPHARDT_BASE}/{lang}/calendar"
    return f"{url}?{urlencode(params)}" if params else url


def ophardt_event_url(event_id: int, lang: str = DEFAULT_LANG) -> str:
    return f"{OPHARDT_BASE}/{lang}/widget/event/{event_id}"


def ophardt_entries_url(event_id: int, lang: str = DEFAULT_LANG) -> str:
    return f"{OPHARDT_BASE}/{lang}/inscriptions/show/{event_id}"


def parse_event_id(value: str | int) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = EVENT_ID_RE.search(text) or WIDGET_EVENT_RE.search(text)
    return int(match.group(1)) if match else None


RANKING_SHOW_RE = re.compile(r"/search/rankings/show/(\d+)")
BIOGRAPHY_RE = re.compile(r"/biography/athlete/(\d+)")
RESULTS_COMPETITION_RE = re.compile(r"/search/results-competition/(\d+)")


def biography_url(athlete_id: int, lang: str = DEFAULT_LANG) -> str:
    return f"{OPHARDT_BASE}/{lang}/biography/athlete/{athlete_id}"


def parse_athlete_id(value: str | int) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = BIOGRAPHY_RE.search(text) or ATHLETE_RE.search(text)
    return int(match.group(1)) if match else None


def rankings_index_url(lang: str = DEFAULT_LANG) -> str:
    return f"{OPHARDT_BASE}/{lang}/search/rankings"


def rankings_federation_url(
    federation_id: int,
    season: int | None = None,
    lang: str = DEFAULT_LANG,
) -> str:
    url = f"{OPHARDT_BASE}/{lang}/search/rankings/{federation_id}"
    if season:
        return f"{url}?season={season}"
    return url


def ranking_show_url(ranking_id: int, lang: str = DEFAULT_LANG) -> str:
    return f"{OPHARDT_BASE}/{lang}/search/rankings/show/{ranking_id}"
