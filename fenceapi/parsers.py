from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup, Tag

from fenceapi.models import (
    AthleteResult,
    Competition,
    CompetitionListing,
    DateRange,
    HomePage,
    ResourceRef,
    Timetable,
    TimetableEntry,
    Tournament,
    TournamentSummary,
)
from fenceapi.urls import (
    ATHLETE_RE,
    BASE_URL,
    INSCRIPTIONS_RE,
    NATION_PATH_RE,
    PARTICIPANT_RE,
    RESOURCE_RE,
    WIDGET_EVENT_RE,
    absolute,
    parse_resource,
)

STATUS_BY_CLASS = {
    "text-success": "current",
    "text-warning": "transferred",
    "text-danger": "no_data",
}

FULL_RANGE_RE = re.compile(
    r"(?P<d1>\d{2})\.(?P<m1>\d{2})\.(?P<y1>\d{4})\s*-\s*(?P<d2>\d{2})\.(?P<m2>\d{2})\.(?P<y2>\d{4})"
)
SHORT_RANGE_RE = re.compile(
    r"(?P<d1>\d{2})\.(?P<m1>\d{2})\.\s*-\s*(?P<d2>\d{2})\.(?P<m2>\d{2})\."
)
LAST_TX_RE = re.compile(
    r"Last transmission:\s*(?P<when>[^|<]+?)(?:\s*\|\s*(?P<who>.+))?$",
    re.I,
)
RANK_RE = re.compile(r"^(T?)(\d+)\.?$", re.I)
NATION_CITY_RE = re.compile(r"^([A-Z]{3})\s+(.+)$")
CITY_NATION_RE = re.compile(r"^(?P<city>.+?)\s*\((?P<nation>[A-Z]{3})\)$")


def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def parse_home(html: str, url: str = f"{BASE_URL}/en/") -> HomePage:
    soup = parse_html(html)
    return HomePage(
        url=url,
        nations=_parse_nation_tabs(soup),
        current=_parse_current_table(soup),
        upcoming=_parse_list_section(soup, "Upcoming", section="upcoming"),
        recent=_parse_list_section(soup, "Archive", section="recent"),
    )


def parse_archive(html: str, url: str) -> list[TournamentSummary]:
    soup = parse_html(html)
    table = soup.select_one("main table")
    if table is None:
        return []
    tournaments: list[TournamentSummary] = []
    for row in table.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        status, live_feed = _status_from(cells[0])
        dates = parse_date_range(cells[1].get_text(" ", strip=True))
        link = cells[2].find("a")
        href = absolute(link["href"]) if link and link.get("href") else url
        title = link.get_text(" ", strip=True) if link else cells[2].get_text(" ", strip=True)
        nation = _nation_from(cells[3])
        city = cells[4].get_text(" ", strip=True) or None
        tournaments.append(
            TournamentSummary(
                title=title,
                url=href,
                status=status,
                live_feed=live_feed,
                date=dates,
                nation=nation,
                city=city,
                resource=_resource_from_url(href),
                ophardt_event_url=_ophardt_event_url(_resource_from_url(href).id),
                section="archive",
            )
        )
    return tournaments


def parse_tournament(html: str, url: str) -> Tournament:
    soup = parse_html(html)
    title_el = soup.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    subtitle_el = soup.find("h2")
    subtitle = subtitle_el.get_text(" ", strip=True) if subtitle_el else None
    crumbs = [li.get_text(" ", strip=True) for li in soup.select("nav[aria-label=breadcrumb] li")]
    nation = None
    city = None
    if crumbs:
        match = CITY_NATION_RE.match(crumbs[-1])
        if match:
            city = match.group("city")
            nation = match.group("nation")
    card = soup.select_one(".card .card-body")
    dates = DateRange()
    if card:
        card_text = card.get_text("\n", strip=True)
        dates = parse_date_range(card_text, fallback_year=_year_from_url(url))
        loc_match = NATION_CITY_RE.search(card_text.replace("\n", " "))
        if loc_match:
            nation = nation or loc_match.group(1)
            city = city or loc_match.group(2).strip()
    parsed = parse_resource(url)
    resource = ResourceRef(
        id=parsed[0] if parsed else None,
        year=parsed[1] if parsed else None,
        slug=parsed[2] if parsed else "tournament",
    )
    return Tournament(
        title=title,
        subtitle=subtitle,
        url=url,
        resource=resource,
        nation=nation,
        city=city,
        date=dates,
        competitions=_parse_competitions(soup, fallback_year=resource.year),
    )


def parse_results(html: str, url: str) -> CompetitionListing:
    return _parse_athlete_table(html, url, page="results", include_rank=True)


def parse_participants(html: str, url: str) -> CompetitionListing:
    return _parse_athlete_table(html, url, page="participants", include_rank=False)


def parse_timetable(html: str, url: str) -> Timetable:
    soup = parse_html(html)
    parsed = parse_resource(url)
    resource = ResourceRef(
        id=parsed[0] if parsed else None,
        year=parsed[1] if parsed else None,
        slug="timetable",
    )
    when, who = _last_transmission(soup)
    entries: list[TimetableEntry] = []
    table = soup.select_one("main table")
    if table:
        for row in table.select("tbody tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 6:
                continue
            pistes_raw = cells[5].get_text(" ", strip=True)
            pistes = [p.strip() for p in pistes_raw.split(",") if p.strip()]
            entries.append(
                TimetableEntry(
                    date=_empty_to_none(cells[0].get_text(" ", strip=True)),
                    time=_empty_to_none(cells[1].get_text(" ", strip=True)),
                    competition=_empty_to_none(cells[2].get_text(" ", strip=True)),
                    phase=_empty_to_none(cells[3].get_text(" ", strip=True)),
                    table=_empty_to_none(cells[4].get_text(" ", strip=True)),
                    pistes=pistes,
                )
            )
    return Timetable(
        resource=resource,
        url=url,
        last_transmission=when,
        transmitter=who,
        entries=entries,
    )


def parse_date_range(text: str, fallback_year: int | None = None) -> DateRange:
    compact = re.sub(r"\s+", " ", text).strip()
    full = FULL_RANGE_RE.search(compact)
    if full:
        start = date(int(full.group("y1")), int(full.group("m1")), int(full.group("d1")))
        end = date(int(full.group("y2")), int(full.group("m2")), int(full.group("d2")))
        return DateRange(start=start.isoformat(), end=end.isoformat(), raw=full.group(0))
    short = SHORT_RANGE_RE.search(compact)
    if short and fallback_year:
        start = date(fallback_year, int(short.group("m1")), int(short.group("d1")))
        end = date(fallback_year, int(short.group("m2")), int(short.group("d2")))
        if end < start:
            end = date(fallback_year + 1, int(short.group("m2")), int(short.group("d2")))
        return DateRange(start=start.isoformat(), end=end.isoformat(), raw=short.group(0))
    return DateRange(raw=compact)


def parse_categories(text: str) -> tuple[list[str], list[str], list[str]]:
    weapons_part, genders_part, ages_part = text, "", ""
    if "|" in text:
        weapons_part, rest = text.split("|", 1)
        if " - " in rest:
            genders_part, ages_part = rest.split(" - ", 1)
        else:
            genders_part = rest
    elif " - " in text:
        weapons_part, ages_part = text.split(" - ", 1)
    return _split_labels(weapons_part), _split_labels(genders_part), _split_labels(ages_part)


def _parse_nation_tabs(soup: BeautifulSoup) -> list[dict[str, str]]:
    nations: list[dict[str, str]] = []
    for link in soup.select(".nav-tabs a.nav-link"):
        href = link.get("href", "")
        match = NATION_PATH_RE.search(href)
        code = match.group(1).upper() if match else ("ALL" if href.rstrip("/").endswith("/en") else None)
        if not code:
            continue
        nations.append(
            {
                "code": code,
                "name": link.get_text(" ", strip=True),
                "url": absolute(href),
            }
        )
    return nations


def _parse_current_table(soup: BeautifulSoup) -> list[TournamentSummary]:
    heading = _heading(soup, "Current tournaments")
    if heading is None:
        return []
    table = heading.find_next("table")
    if table is None:
        return []
    items: list[TournamentSummary] = []
    for row in table.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        items.append(_summary_from_current_row(cells))
    return items


def _summary_from_current_row(cells: list[Tag]) -> TournamentSummary:
    status, live_feed = _status_from(cells[0])
    if cells[0].find("i", class_="fa-external-link"):
        status = "external"
    title_link = cells[1].find("a")
    href = absolute(title_link["href"]) if title_link and title_link.get("href") else ""
    resource = _resource_from_url(href)
    ophardt_url = None
    inscriptions_url = None
    external_url = None
    if title_link and title_link.get("href", "").startswith("http") and "fencingworldwide.com" not in title_link["href"]:
        external_url = title_link["href"]
    if len(cells) > 2:
        for link in cells[2].find_all("a"):
            href_l = link.get("href", "")
            if WIDGET_EVENT_RE.search(href_l):
                ophardt_url = href_l
                if resource.id is None:
                    resource.id = int(WIDGET_EVENT_RE.search(href_l).group(1))
            elif INSCRIPTIONS_RE.search(href_l):
                inscriptions_url = href_l
    fallback_year = resource.year or date.today().year
    dates = parse_date_range(cells[0].get_text(" ", strip=True), fallback_year=fallback_year)
    if resource.year is None and dates.start:
        resource.year = int(dates.start[:4])
    title = title_link.get_text(" ", strip=True) if title_link else cells[1].get_text(" ", strip=True)
    nation, city = _nation_city_from(cells[1])
    small = cells[1].find("small")
    categories_raw = small.get_text(" ", strip=True) if small else None
    weapons, genders, ages = parse_categories(categories_raw or "")
    return TournamentSummary(
        title=title,
        url=href or ophardt_url or "",
        status=status,
        live_feed=live_feed,
        date=dates,
        nation=nation,
        city=city,
        weapons=weapons,
        genders=genders,
        age_classes=ages,
        categories_raw=categories_raw,
        resource=resource,
        ophardt_event_url=ophardt_url or _ophardt_event_url(resource.id),
        inscriptions_url=inscriptions_url,
        external_url=external_url,
        section="current",
    )


def _parse_list_section(soup: BeautifulSoup, heading_text: str, section: str) -> list[TournamentSummary]:
    heading = _heading(soup, heading_text)
    if heading is None:
        return []
    listing = heading.find_next("ul", class_="list-group")
    if listing is None:
        return []
    items: list[TournamentSummary] = []
    for li in listing.find_all("li", recursive=False):
        title_link = li.find("a")
        href = absolute(title_link["href"]) if title_link and title_link.get("href") else ""
        resource = _resource_from_url(href)
        ophardt_url = None
        inscriptions_url = None
        for link in li.find_all("a"):
            href_l = link.get("href", "")
            if WIDGET_EVENT_RE.search(href_l):
                ophardt_url = href_l
            elif INSCRIPTIONS_RE.search(href_l):
                inscriptions_url = href_l
        dates = parse_date_range(li.get_text(" ", strip=True), fallback_year=resource.year)
        nation, city = _nation_city_from(li)
        status, live_feed = _status_from(li)
        if section == "recent":
            status = "archive"
        items.append(
            TournamentSummary(
                title=title_link.get_text(" ", strip=True) if title_link else li.get_text(" ", strip=True),
                url=href,
                status=status or ("upcoming" if section == "upcoming" else "archive"),
                live_feed=live_feed,
                date=dates,
                nation=nation,
                city=city,
                resource=resource,
                ophardt_event_url=ophardt_url or _ophardt_event_url(resource.id),
                inscriptions_url=inscriptions_url,
                section=section,
            )
        )
    return items


def _parse_competitions(soup: BeautifulSoup, fallback_year: int | None) -> list[Competition]:
    competitions: list[Competition] = []
    seen: set[str] = set()
    current_age = None
    root = soup.find("main") or soup
    for heading in root.find_all(["h5", "h6"]):
        if heading.name == "h5":
            current_age = heading.get_text(" ", strip=True)
            continue
        weapon = heading.get_text(" ", strip=True)
        listing = heading.find_next_sibling("ul")
        if listing is None:
            continue
        for item in listing.select("li.list-group-item"):
            link = item.find("a")
            if not link or not link.get("href"):
                continue
            href = absolute(link["href"])
            if href in seen:
                continue
            seen.add(href)
            parsed = parse_resource(href)
            title = link.get_text(" ", strip=True)
            kind = "team" if "team" in title.lower() else "individual"
            badge = item.find("span", class_="badge")
            status, live_feed = _status_from(item)
            competitions.append(
                Competition(
                    title=title,
                    url=href,
                    resource=ResourceRef(
                        id=parsed[0] if parsed else None,
                        year=parsed[1] if parsed else fallback_year,
                        slug=parsed[2] if parsed else "global",
                    ),
                    weapon=weapon,
                    gender=_gender_from(title),
                    age_class=current_age,
                    kind=kind,
                    start_at=badge.get_text(" ", strip=True) if badge else None,
                    status=status,
                    live_feed=live_feed,
                )
            )
    if competitions:
        return competitions
    for link in soup.select(".dropdown-menu a.dropdown-item"):
        href = absolute(link.get("href", ""))
        if href in seen or not RESOURCE_RE.search(href):
            continue
        seen.add(href)
        parsed = parse_resource(href)
        label = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
        label = re.sub(r"^\d{2}\.\d{2}\.:\s*", "", label)
        competitions.append(
            Competition(
                title=label,
                url=href,
                resource=ResourceRef(
                    id=parsed[0] if parsed else None,
                    year=parsed[1] if parsed else fallback_year,
                    slug=parsed[2] if parsed else "global",
                ),
            )
        )
    return competitions


def _parse_athlete_table(
    html: str,
    url: str,
    page: str,
    include_rank: bool,
) -> CompetitionListing:
    soup = parse_html(html)
    parsed = parse_resource(url)
    resource = ResourceRef(
        id=parsed[0] if parsed else None,
        year=parsed[1] if parsed else None,
        slug=page,
    )
    when, who = _last_transmission(soup)
    rows: list[AthleteResult] = []
    table = soup.select_one("main table.startlist, main table")
    if table is None:
        return CompetitionListing(resource=resource, page=page, url=url, last_transmission=when, transmitter=who)
    headers = [_norm_header(th.get_text(" ", strip=True)) for th in table.select("thead th")]
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        rank = None
        rank_value = None
        tied = False
        offset = 0
        if include_rank:
            rank_text = cells[0].get_text(" ", strip=True)
            rank, rank_value, tied = _parse_rank(rank_text)
            offset = 1 if rank_text else 0
        nation_cell = _cell_by_header(cells, headers, "nation", fallback=offset)
        name_cell = _cell_by_header(cells, headers, "name", fallback=offset + 1 if len(cells) > offset + 1 else offset)
        club_cell = _cell_by_header(cells, headers, "club")
        region_cell = _cell_by_header(cells, headers, "reg")
        seed = None
        present = None
        leftover = cells[offset + 2 :] if include_rank else cells[2:]
        for cell in leftover:
            text = cell.get_text(" ", strip=True)
            if cell.find("i", class_="fa-check"):
                present = True
            elif re.fullmatch(r"\d+(?:\.\d+)?", text):
                seed = text
        name_link = name_cell.find("a", href=ATHLETE_RE) if name_cell else None
        participant_link = name_cell.find("a", href=PARTICIPANT_RE) if name_cell else None
        athlete_id = None
        if name_link:
            athlete_id = int(ATHLETE_RE.search(name_link["href"]).group(1))
        elif participant_link:
            athlete_id = int(PARTICIPANT_RE.search(participant_link["href"]).group(1))
        name = ""
        if name_link:
            name = name_link.get_text(" ", strip=True)
        elif name_cell:
            name = name_cell.get_text(" ", strip=True)
        rows.append(
            AthleteResult(
                rank=rank,
                rank_value=rank_value,
                tied=tied,
                nation=_nation_from(nation_cell) if nation_cell else None,
                name=name,
                athlete_id=athlete_id,
                club=club_cell.get_text(" ", strip=True) if club_cell else None,
                region=region_cell.get_text(" ", strip=True) if region_cell else None,
                seed=seed,
                present=present,
            )
        )
    return CompetitionListing(
        resource=resource,
        page=page,
        url=url,
        last_transmission=when,
        transmitter=who,
        rows=rows,
    )


def _heading(soup: BeautifulSoup, text: str) -> Tag | None:
    for tag in soup.find_all(["h4", "h3", "h2"]):
        if tag.get_text(" ", strip=True).startswith(text):
            return tag
    return None


def _status_from(node: Tag | None) -> tuple[str, bool]:
    if node is None:
        return "unknown", False
    live_feed = bool(node.find("i", class_="fa-lightbulb"))
    if node.find("i", class_="fa-archive"):
        return "archive", live_feed
    if node.find("i", class_="fa-external-link"):
        return "external", live_feed
    icon = node.find("i", class_=re.compile(r"text-(success|warning|danger)"))
    if icon:
        classes = icon.get("class", [])
        for css, status in STATUS_BY_CLASS.items():
            if css in classes:
                return status, live_feed or ("fa-lightbulb" in classes)
    return ("live" if live_feed else "unknown"), live_feed


def _nation_from(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = node.get_text(" ", strip=True)
    match = re.search(r"\b([A-Z]{3})\b", text)
    if match:
        return match.group(1)
    img = node.find("img", src=re.compile(r"/flags/([a-z]{3})\.svg", re.I))
    if img:
        flag = re.search(r"/flags/([a-z]{3})\.svg", img.get("src", ""), re.I)
        if flag:
            return flag.group(1).upper()
    return None


def _nation_city_from(node: Tag) -> tuple[str | None, str | None]:
    img = node.find("img", src=re.compile(r"/flags/", re.I))
    nation = None
    city = None
    if img:
        after = " ".join(t.strip() for t in img.next_siblings if isinstance(t, str))
        match = NATION_CITY_RE.match(after.strip())
        if match:
            nation, city = match.group(1), match.group(2).strip()
        else:
            city = after.strip() or None
            nation = _nation_from(node)
    if nation is None:
        nation = _nation_from(node)
    return nation, city


def _resource_from_url(url: str) -> ResourceRef:
    parsed = parse_resource(url)
    if not parsed:
        return ResourceRef()
    return ResourceRef(id=parsed[0], year=parsed[1], slug=parsed[2])


def _year_from_url(url: str) -> int | None:
    parsed = parse_resource(url)
    return parsed[1] if parsed else None


def _ophardt_event_url(event_id: int | None) -> str | None:
    if event_id is None:
        return None
    return f"https://fencing.ophardt.online/en/widget/event/{event_id}"


def _last_transmission(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        match = LAST_TX_RE.search(text)
        if match:
            when = match.group("when").strip()
            who = (match.group("who") or "").strip() or None
            return when, who
    return None, None


def _parse_rank(text: str) -> tuple[str | None, int | None, bool]:
    text = text.strip()
    if not text:
        return None, None, False
    match = RANK_RE.match(text)
    if not match:
        return text, None, False
    tied = bool(match.group(1))
    value = int(match.group(2))
    return text.rstrip("."), value, tied


def _cell_by_header(cells: list[Tag], headers: list[str], name: str, fallback: int | None = None) -> Tag | None:
    try:
        index = headers.index(name)
        if index < len(cells):
            return cells[index]
    except ValueError:
        pass
    if fallback is not None and fallback < len(cells):
        return cells[fallback]
    return None


def _norm_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _gender_from(title: str) -> str | None:
    lowered = title.lower()
    if "women" in lowered:
        return "women"
    if "men" in lowered:
        return "men"
    if "mixed" in lowered:
        return "mixed"
    if "open" in lowered:
        return "open"
    return None


def _split_labels(text: str) -> list[str]:
    parts = [part.strip(" .") for part in re.split(r"[,/]", text) if part.strip(" .")]
    return parts


def _empty_to_none(value: str) -> str | None:
    cleaned = value.replace("\xa0", " ").strip()
    return cleaned or None
