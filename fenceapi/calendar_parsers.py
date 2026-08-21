from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from fenceapi.models import (
    CalendarEvent,
    EventCompetition,
    EventCompetitionEntries,
    EventDetail,
    EventEntry,
    RelatedEvent,
)
from fenceapi.ranking_parsers import WEAPON_ALIASES, GENDER_ALIASES
from fenceapi.urls import OPHARDT_BASE, absolute, parse_event_id


MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
EN_DATE_RE = re.compile(
    r"(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s+(20\d{2})",
    re.I,
)
NATION_CLUB_RE = re.compile(r"^([A-Z]{3})\s+(.+)$")
YOB_RE = re.compile(r"^(19|20)\d{2}$")
MASTER_COMP_RE = re.compile(r"results-mastercompetition/(\d+)")


def parse_calendar(html: str, url: str) -> list[CalendarEvent]:
    soup = BeautifulSoup(html, "html.parser")
    events: list[CalendarEvent] = []
    seen: set[int] = set()
    table = soup.select_one("table.table")
    if table is None:
        return events
    for row in table.select("tbody tr"):
        classes = " ".join(row.get("class") or [])
        if "bg-info" in classes:
            continue
        event = _parse_calendar_row(row)
        if event is None or event.event_id in seen:
            continue
        seen.add(event.event_id)
        events.append(event)
    return events


def parse_event(html: str, url: str, event_id: int | None = None) -> EventDetail:
    soup = BeautifulSoup(html, "html.parser")
    resolved_id = event_id or parse_event_id(url) or 0
    heading = soup.select_one("h1")
    title, subtitle = _split_heading(heading)
    leads = soup.select("p.lead")
    date_start, date_end = parse_en_dates(_text(leads[0]) if leads else "")
    nation, region, city = _parse_location(_text(leads[1]) if len(leads) > 1 else "")

    invitation = _first_href(soup, href_contains="/invitation/")
    entries_url = _first_href(soup, href_contains="/inscriptions/show/")
    results_url = _first_href(soup, href_contains="/search/results/")
    live = soup.find("a", href=re.compile(r"fencingworldwide\.com/.+/tournament", re.I))
    live_url = absolute(live["href"], OPHARDT_BASE) if live and live.get("href") else None

    entries_open = entries_close = None
    for paragraph in soup.select("div.buttons p"):
        raw = re.sub(r"\s+", " ", paragraph.get_text(" ", strip=True))
        if raw.lower().startswith("entries:"):
            window = raw.split(":", 1)[1].split("Deadlines")[0].strip()
            entries_open, entries_close = parse_en_dates(window)
            break

    competitions = _parse_competitions(soup)
    related = _parse_related(soup)
    return EventDetail(
        event_id=resolved_id,
        url=url,
        title=title,
        subtitle=subtitle,
        date_start=date_start,
        date_end=date_end,
        nation=nation,
        region=region,
        city=city,
        invitation_url=invitation,
        entries_url=entries_url,
        results_url=results_url,
        live_results_url=live_url,
        entries_open=entries_open,
        entries_close=entries_close,
        competitions=competitions,
        related=related,
    )


def parse_inscriptions(html: str, url: str) -> list[EventCompetitionEntries]:
    soup = BeautifulSoup(html, "html.parser")
    groups: list[EventCompetitionEntries] = []
    for heading in soup.select("h2"):
        title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
        if not title or title.lower().startswith("referee"):
            continue
        stats = heading.select_one(".ajaxstats")
        competition_id = None
        if stats and str(stats.get("data-id", "")).isdigit():
            competition_id = int(stats["data-id"])
        table = heading.find_next("table")
        if table is None:
            continue
        header = table.find("th")
        header_text = header.get_text(" ", strip=True).lower() if header else ""
        if "athlete" not in header_text and "team" not in header_text:
            continue
        entries = [_parse_entry_row(row) for row in table.select("tr") if row.find("td")]
        groups.append(
            EventCompetitionEntries(
                title=title,
                competition_id=competition_id,
                entries=[item for item in entries if item is not None],
            )
        )
    return groups


def parse_en_dates(text: str) -> tuple[str | None, str | None]:
    found = [ _format_en_date(match) for match in EN_DATE_RE.finditer(text or "") ]
    if not found:
        return None, None
    if len(found) == 1:
        return found[0], found[0]
    return found[0], found[-1]


def _parse_calendar_row(row: Tag) -> CalendarEvent | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 13:
        return None
    link = row.find("a", href=re.compile(r"/widget/event/\d+"))
    event_id = parse_event_id(link.get("href", "") if link else "")
    if event_id is None:
        return None
    title_cell = cells[8]
    title_link = title_cell.find("a")
    smalls = [re.sub(r"\s+", " ", tag.get_text(" ", strip=True)) for tag in title_cell.find_all("small")]
    main = ""
    if title_link is not None:
        bits = []
        for child in title_link.children:
            if getattr(child, "name", None) == "small":
                continue
            text = child.get_text(" ", strip=True) if isinstance(child, Tag) else str(child).strip()
            if text:
                bits.append(re.sub(r"\s+", " ", text))
        main = " ".join(bits).strip()
    organizer = smalls[0] if smalls else None
    extra = smalls[1] if len(smalls) > 1 else None
    subtitle = " — ".join(part for part in (organizer, extra) if part) or None
    date_start, date_end = parse_en_dates(cells[5].get("title") or _text(cells[5]))
    nation, region, _ = _parse_location(_text(cells[6]))
    city = cells[7].get("title") or _text(cells[7]) or None
    age_classes = [part for part in _text(cells[9]).split() if part]
    weapons = {
        "epee": _genders_from(cells[10]),
        "foil": _genders_from(cells[11]),
        "sabre": _genders_from(cells[12]),
    }
    invitation = _first_href_in(cells[3])
    ics = _first_href_in(cells[13]) if len(cells) > 13 else None
    status_icon = cells[1].find("i")
    status = None
    if status_icon is not None:
        status = (status_icon.get("title") or "").strip() or None
    refresh = cells[0].find("i", class_=re.compile("fa-refresh"))
    updated = None
    if refresh is not None:
        updated = (refresh.get("title") or "").replace("Updated tournament", "").strip() or None
    open_for = _text(cells[4]) or None
    return CalendarEvent(
        event_id=event_id,
        url=absolute(f"/en/widget/event/{event_id}", OPHARDT_BASE),
        title=main or (organizer or f"Event {event_id}"),
        subtitle=subtitle,
        date_start=date_start,
        date_end=date_end,
        nation=nation,
        region=region or None,
        city=city,
        age_classes=age_classes,
        weapons={key: value for key, value in weapons.items() if value},
        invitation_url=absolute(invitation, OPHARDT_BASE) if invitation else None,
        ics_url=absolute(ics, OPHARDT_BASE) if ics else None,
        status=status,
        updated=updated,
        open_for=open_for,
    )


def _parse_competitions(soup: BeautifulSoup) -> list[EventCompetition]:
    heading = soup.find(["h2", "h3"], string=re.compile(r"Competitions", re.I))
    table = heading.find_next("table") if heading else soup.select_one("h3 + table, table.table")
    if table is None:
        return []
    items: list[EventCompetition] = []
    last_day: str | None = None
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        day = _text(cells[0]) or last_day
        if _text(cells[0]):
            last_day = _text(cells[0])
        weapon = _norm_weapon(_text(cells[1]))
        gender = _norm_gender(_text(cells[2]))
        kind = _text(cells[4]).lower() or None
        age = _text(cells[5]) or None
        birth_years = _text(cells[6]) if len(cells) > 6 else None
        medalists = None
        entries_url = None
        master_id = None
        for cell in cells:
            for anchor in cell.find_all("a", href=True):
                href = anchor["href"]
                if "inscriptions" in href and entries_url is None:
                    entries_url = absolute(href, OPHARDT_BASE)
                match = MASTER_COMP_RE.search(href)
                if match:
                    master_id = int(match.group(1))
                    medalists = absolute(href, OPHARDT_BASE)
        items.append(
            EventCompetition(
                day=day,
                weapon=weapon,
                gender=gender,
                kind=kind,
                age_class=age,
                birth_years=birth_years or None,
                master_competition_id=master_id,
                entries_url=entries_url,
                medalists_url=medalists,
            )
        )
    return items


def _parse_related(soup: BeautifulSoup) -> list[RelatedEvent]:
    heading = soup.find(["h5", "h4", "h3"], string=re.compile(r"Other dates", re.I))
    if heading is None:
        return []
    related: list[RelatedEvent] = []
    for item in heading.find_next("ul").select("a") if heading.find_next("ul") else []:
        href = item.get("href") or ""
        event_id = parse_event_id(href)
        if event_id is None:
            continue
        classes = " ".join(item.get("class") or [])
        small = item.find("small")
        location = _text(small) if small else None
        dates = _text(item).replace(location or "", "").strip() or None
        related.append(
            RelatedEvent(
                event_id=event_id,
                url=absolute(href, OPHARDT_BASE),
                dates=dates,
                location=location,
                current="active" in classes,
            )
        )
    return related


def _parse_entry_row(row: Tag) -> EventEntry | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 3:
        return None
    name = _text(cells[0])
    if not name:
        return None
    yob_text = _text(cells[1])
    yob = int(yob_text) if YOB_RE.match(yob_text) else None
    nation = club = None
    club_text = re.sub(r"\s+", " ", cells[2].get_text(" ", strip=True))
    match = NATION_CLUB_RE.match(club_text)
    if match:
        nation, club = match.group(1), match.group(2).strip() or None
    elif club_text:
        club = club_text
    seeding = _text(cells[5]) if len(cells) > 5 else None
    if seeding == "---":
        seeding = None
    status = _text(cells[6]) if len(cells) > 6 else None
    license_valid = None
    if len(cells) > 7:
        icon = cells[7].find("i")
        if icon is not None:
            title = (icon.get("title") or "").lower()
            color = (icon.get("style") or "").lower()
            license_valid = "valid" in title or "green" in color
    return EventEntry(
        name=name,
        year_of_birth=yob,
        nation=nation,
        club=club,
        status=status or None,
        seeding=seeding,
        license_valid=license_valid,
    )


def _genders_from(cell: Tag) -> list[str]:
    genders: list[str] = []
    for icon in cell.find_all("i"):
        classes = " ".join(icon.get("class") or [])
        if "fa-venus-mars" in classes:
            genders.append("open")
        elif "fa-mars" in classes:
            genders.append("men")
        elif "fa-venus" in classes:
            genders.append("women")
    return genders


def _parse_location(text: str) -> tuple[str | None, str | None, str | None]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return None, None, None
    parts = cleaned.split(" ", 2)
    nation = parts[0].upper() if re.fullmatch(r"[A-Za-z]{3}", parts[0]) else None
    if nation is None:
        return None, None, cleaned
    rest = parts[1:] 
    region = None
    city = None
    if rest and re.fullmatch(r"[A-Z]{1,4}", rest[0]):
        region = rest[0]
        city = rest[1] if len(rest) > 1 else None
    else:
        city = " ".join(rest) or None
    return nation, region, city


def _split_heading(heading: Tag | None) -> tuple[str, str | None]:
    if heading is None:
        return "", None
    smalls = [re.sub(r"\s+", " ", tag.get_text(" ", strip=True)) for tag in heading.find_all("small")]
    bits = []
    for child in heading.children:
        if getattr(child, "name", None) == "small":
            continue
        text = child.get_text(" ", strip=True) if isinstance(child, Tag) else str(child).strip()
        if text:
            bits.append(re.sub(r"\s+", " ", text))
    title = " ".join(bits).strip() or (smalls[0] if smalls else "")
    extra = [part for part in smalls if part and part != title]
    return title, " — ".join(extra) if extra else None


def _first_href(soup: BeautifulSoup, href_contains: str) -> str | None:
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        if href_contains in href:
            return absolute(href, OPHARDT_BASE)
    return None


def _first_href_in(cell: Tag) -> str | None:
    anchor = cell.find("a", href=True)
    return anchor["href"] if anchor else None


def _norm_weapon(value: str) -> str | None:
    key = value.strip().lower()
    return WEAPON_ALIASES.get(key, key or None)


def _norm_gender(value: str) -> str | None:
    key = value.strip().lower().replace("’", "'")
    return GENDER_ALIASES.get(key, key or None)


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _format_en_date(match: re.Match[str]) -> str:
    parsed = datetime.strptime(
        f"{match.group(1).title()} {int(match.group(2)):02d} {match.group(3)}",
        "%b %d %Y",
    )
    return parsed.date().isoformat()
