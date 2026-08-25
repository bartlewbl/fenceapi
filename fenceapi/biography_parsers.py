from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from fenceapi.calendar_parsers import parse_en_dates
from fenceapi.models import (
    AthleteCompetitionResult,
    AthleteExam,
    AthleteMembership,
    AthleteProfile,
    AthleteResultGroup,
    AthleteSeasonRanking,
    AthleteSelection,
    MedalCount,
)
from fenceapi.ranking_parsers import (
    normalize_age,
    normalize_gender,
    normalize_kind,
    normalize_weapon,
)
from fenceapi.urls import (
    BIOGRAPHY_RE,
    OPHARDT_BASE,
    RANKING_SHOW_RE,
    RESULTS_COMPETITION_RE,
    absolute,
    parse_athlete_id,
)

US_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
CLUB_NATION_RE = re.compile(r"^(.+?)\s+\(([A-Z]{3})\)$")
SEASON_RE = re.compile(r"^(20\d{2})\s*/\s*(20\d{2})$")
METRIC_KEYS = {
    "wins round": "wins_round",
    "losses round": "losses_round",
    "wins elimination direct": "wins_elimination",
    "losses elimination direct": "losses_elimination",
    "total hits": "total_hits",
    "average hits winning round": "avg_hits_win_round",
    "average hits losing round": "avg_hits_lose_round",
    "average hits winning elimination direct": "avg_hits_win_elimination",
    "average hits losing elimination direct": "avg_hits_lose_elimination",
}


def parse_biography(html: str, url: str, athlete_id: int | None = None) -> AthleteProfile:
    soup = BeautifulSoup(html, "html.parser")
    resolved = athlete_id or parse_athlete_id(url) or _id_from_soup(soup) or 0
    header = soup.select_one(".bios_header") or soup
    name = _text(header.select_one("h1"))
    nation = _header_nation(header)
    clubs = _header_clubs(header)
    weapons = _header_weapons(header)
    age = _header_age(header)
    gender = _header_gender(header)
    photo_url = _header_photo(header)
    return AthleteProfile(
        athlete_id=resolved,
        url=url,
        name=name,
        nation=nation,
        clubs=clubs,
        weapons=weapons,
        age=age,
        gender=gender,
        photo_url=photo_url,
        medals=_parse_medals(soup.select_one("#overview")),
        exams=_parse_exams(soup.select_one("#overview")),
        results=_parse_results(soup.select_one("#results")),
        match_stats=_parse_match_stats(soup.select_one("#matches")),
        season_rankings=_parse_season_rankings(soup.select_one("#rankings")),
        selections=_parse_selections(soup.select_one("#selections")),
        memberships=_parse_memberships(soup.select_one("#memberships")),
    )


def _id_from_soup(soup: BeautifulSoup) -> int | None:
    for anchor in soup.select("a[href]"):
        match = BIOGRAPHY_RE.search(anchor.get("href") or "")
        if match:
            return int(match.group(1))
    return None


def _header_nation(header: Tag) -> str | None:
    heading = header.select_one("h3")
    text = _text(heading)
    if re.fullmatch(r"[A-Z]{3}", text):
        return text
    flag = header.select_one("img[src*='/flags/']")
    if flag and flag.get("src"):
        match = re.search(r"/flags/([a-z]{3})\.", flag["src"], re.I)
        if match:
            return match.group(1).upper()
    return None


def _header_clubs(header: Tag) -> str | None:
    heading = header.select_one("h1")
    if heading is None:
        return None
    paragraph = heading.find_next_sibling("p")
    text = _text(paragraph)
    return text or None


def _header_weapons(header: Tag) -> list[str]:
    weapons: list[str] = []
    seen: set[str] = set()
    for span in header.select(".bg-success"):
        raw = _text(span)
        if not raw:
            continue
        try:
            weapon = normalize_weapon(raw)
        except ValueError:
            weapon = raw.lower()
        if weapon in seen:
            continue
        seen.add(weapon)
        weapons.append(weapon)
    return weapons


def _header_age(header: Tag) -> int | None:
    box = header.select_one("[title=Age], [title=age]")
    if box is None:
        return None
    match = re.search(r"\b(\d{1,2})\b", _text(box))
    return int(match.group(1)) if match else None


def _header_gender(header: Tag) -> str | None:
    if header.select_one(".fa-mars"):
        return "men"
    if header.select_one(".fa-venus"):
        return "women"
    for box in header.select("[title]"):
        title = str(box.get("title") or "")
        try:
            return normalize_gender(title)
        except ValueError:
            continue
    return None


def _header_photo(header: Tag) -> str | None:
    lightbox = header.select_one("a[data-toggle=lightbox][href]")
    if lightbox:
        return absolute(lightbox["href"], OPHARDT_BASE)
    image = header.select_one("img[src*='athlete']") or header.select_one(".col-md-2 img[src]")
    if image and image.get("src"):
        return absolute(image["src"], OPHARDT_BASE)
    return None


def _parse_medals(overview: Tag | None) -> list[MedalCount]:
    if overview is None:
        return []
    medals: list[MedalCount] = []
    heading = _find_heading(overview, "h3", "medals")
    root = heading.find_next("div", class_="row") if heading else overview
    if root is None:
        return []
    for table in root.select("table"):
        title = _text(table.select_one("th[colspan]")) or _text(table.select_one("th"))
        cells = table.select("tbody td") or table.select("td")
        if not title or len(cells) < 3:
            continue
        gold, silver, bronze = (_maybe_int(_text(cell)) or 0 for cell in cells[:3])
        medals.append(MedalCount(title=title, gold=gold, silver=silver, bronze=bronze))
    return medals


def _parse_exams(overview: Tag | None) -> list[AthleteExam]:
    if overview is None:
        return []
    heading = _find_heading(overview, "h4", "my exams")
    if heading is None:
        return []
    table = heading.find_next("table")
    if table is None:
        return []
    exams: list[AthleteExam] = []
    for row in table.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        date_text = _text(cells[1])
        name = _text(cells[2])
        if not name:
            continue
        start, _ = parse_en_dates(date_text)
        exams.append(AthleteExam(date=start, name=name))
    return exams


def _parse_results(panel: Tag | None) -> list[AthleteResultGroup]:
    if panel is None:
        return []
    groups: list[AthleteResultGroup] = []
    for heading in panel.select("h5"):
        title = _text(heading)
        if not title:
            continue
        rows: list[AthleteCompetitionResult] = []
        seen: set[tuple[Any, ...]] = set()
        for table in _following_tables(heading, stop=("h5",)):
            for item in _parse_result_rows(table):
                key = (
                    item.competition_id,
                    item.rank,
                    item.date_start,
                    item.category,
                    item.competition,
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(item)
        groups.append(AthleteResultGroup(group=title, results=rows))
    return groups


def _parse_result_rows(table: Tag) -> list[AthleteCompetitionResult]:
    results: list[AthleteCompetitionResult] = []
    for row in table.find_all("tr"):
        cells = _result_cells(row)
        if cells is None:
            continue
        rank = _maybe_int(_text(cells[0]))
        date_cell = cells[1]
        date_text = _direct_text(date_cell) if date_cell.find("td") else _text(date_cell)
        date_start, date_end = parse_us_dates(date_text)
        city_cell = cells[2]
        city = _text(city_cell)
        nation = _flag_nation(city_cell)
        competition_link = cells[3].find("a", href=True) or cells[2].find("a", href=True)
        competition = _text(cells[3])
        href = competition_link.get("href") if competition_link else None
        competition_id = None
        url = None
        if href:
            url = absolute(href.split("?")[0], OPHARDT_BASE)
            match = RESULTS_COMPETITION_RE.search(href)
            if match:
                competition_id = int(match.group(1))
        category = _text(cells[4])
        weapon, gender, age_class, kind = parse_category_label(category)
        results.append(
            AthleteCompetitionResult(
                rank=rank,
                date_start=date_start,
                date_end=date_end,
                city=city or None,
                nation=nation,
                competition=competition,
                category=category,
                competition_id=competition_id,
                url=url,
                weapon=weapon,
                gender=gender,
                age_class=age_class,
                kind=kind,
            )
        )
    return results


def _result_cells(row: Tag) -> list[Tag] | None:
    """Ophardt highlight tables omit a closing date </td>, nesting the later cells."""
    direct = row.find_all("td", recursive=False)
    if len(direct) >= 5:
        return direct[:5]
    leaves = [td for td in row.find_all("td") if td.find("td") is None]
    if len(direct) >= 2 and len(leaves) >= 4:
        rank = direct[0]
        rest = [td for td in leaves if td is not rank]
        if len(rest) < 3:
            return None
        return [rank, direct[1], rest[0], rest[1], rest[2]]
    if len(leaves) >= 5:
        return leaves[:5]
    return None


def _parse_match_stats(panel: Tag | None) -> list[dict[str, Any]]:
    if panel is None:
        return []
    table = panel.select_one("table")
    if table is None:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    headers = [_text(cell) for cell in rows[0].find_all(["th", "td"])]
    seasons = [value for value in headers[1:] if value]
    stats: list[dict[str, Any]] = []
    for season in seasons:
        item: dict[str, Any] = {"season": season}
        match = SEASON_RE.fullmatch(season.replace(" ", ""))
        if match:
            item["season_start"] = int(match.group(1))
        stats.append(item)
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        key = METRIC_KEYS.get(_fold_metric(_text(cells[0])))
        if key is None:
            continue
        for index, cell in enumerate(cells[1:]):
            if index >= len(stats):
                break
            stats[index][key] = _maybe_metric(_text(cell))
    return stats


def _parse_season_rankings(panel: Tag | None) -> list[AthleteSeasonRanking]:
    if panel is None:
        return []
    rankings: list[AthleteSeasonRanking] = []
    for heading in panel.select("h4"):
        level = _text(heading)
        table = heading.find_next("table")
        if not level or table is None:
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 5:
                continue
            link = cells[4].find("a", href=True)
            href = link.get("href") if link else None
            ranking_id = None
            url = None
            if href:
                url = absolute(href, OPHARDT_BASE)
                match = RANKING_SHOW_RE.search(href)
                if match:
                    ranking_id = int(match.group(1))
            category = _text(cells[4])
            weapon, gender, age_class, kind = parse_category_label(category)
            rankings.append(
                AthleteSeasonRanking(
                    rank=_maybe_int(_text(cells[0])),
                    points=_maybe_float(_text(cells[1])),
                    season=_maybe_int(_text(cells[2])),
                    title=_text(cells[3]),
                    level=level,
                    category=category,
                    ranking_id=ranking_id,
                    url=url,
                    weapon=weapon,
                    gender=gender,
                    age_class=age_class,
                    kind=kind,
                )
            )
    return rankings


def _parse_selections(panel: Tag | None) -> list[AthleteSelection]:
    if panel is None:
        return []
    table = panel.select_one("table")
    if table is None:
        return []
    selections: list[AthleteSelection] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        season = _text(cells[0])
        selection = _text(cells[1])
        if not season or not selection:
            continue
        weapon = None
        raw_weapon = _text(cells[2]) if len(cells) > 2 else ""
        if raw_weapon:
            try:
                weapon = normalize_weapon(raw_weapon)
            except ValueError:
                weapon = raw_weapon.lower()
        selections.append(
            AthleteSelection(
                season=season,
                selection=selection,
                weapon=weapon,
                federation=_text(cells[3]) or None if len(cells) > 3 else None,
                training_center=_text(cells[4]) or None if len(cells) > 4 else None,
                coach=_text(cells[5]) or None if len(cells) > 5 else None,
            )
        )
    return selections


def _parse_memberships(panel: Tag | None) -> list[AthleteMembership]:
    if panel is None:
        return []
    table = panel.select_one("table")
    if table is None:
        return []
    memberships: list[AthleteMembership] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        raw_club = _text(cells[0])
        if not raw_club:
            continue
        club, nation = _split_club_nation(raw_club)
        start, _ = parse_en_dates(_text(cells[2]))
        end = None
        if len(cells) > 3:
            end, _ = parse_en_dates(_text(cells[3]))
        note = _text(cells[4]) if len(cells) > 4 else ""
        memberships.append(
            AthleteMembership(
                club=club,
                nation=nation,
                type=_text(cells[1]) or None if len(cells) > 1 else None,
                start=start,
                end=end,
                note=note or None,
            )
        )
    return memberships


def parse_category_label(
    text: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    weapon = gender = age = kind = None
    leftover: list[str] = []
    for token in text.split():
        if weapon is None:
            try:
                weapon = normalize_weapon(token)
                continue
            except ValueError:
                pass
        if gender is None:
            try:
                gender = normalize_gender(token)
                continue
            except ValueError:
                pass
        if kind is None:
            try:
                parsed_kind = normalize_kind(token)
            except ValueError:
                parsed_kind = None
            if parsed_kind in {"individual", "team"}:
                kind = parsed_kind
                continue
        leftover.append(token)
    if leftover:
        raw_age = re.sub(r"\s+[A-Z]$", "", " ".join(leftover)).strip()
        if raw_age:
            age = normalize_age(raw_age)
    return weapon, gender, age, kind


def parse_us_dates(text: str) -> tuple[str | None, str | None]:
    found = [format_us_date(match) for match in US_DATE_RE.finditer(text or "")]
    if not found:
        return None, None
    if len(found) == 1:
        return found[0], found[0]
    return found[0], found[-1]


def format_us_date(match: re.Match[str]) -> str:
    month, day, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if year < 100:
        year += 2000 if year < 70 else 1900
    return f"{year:04d}-{month:02d}-{day:02d}"


def _following_tables(heading: Tag, stop: tuple[str, ...]) -> list[Tag]:
    tables: list[Tag] = []
    for sibling in heading.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if sibling.name in stop:
            break
        if sibling.name == "table":
            tables.append(sibling)
            continue
        tables.extend(sibling.select("table"))
    return tables


def _find_heading(root: Tag, name: str, title: str) -> Tag | None:
    wanted = title.casefold()
    for heading in root.select(name):
        if _text(heading).casefold() == wanted:
            return heading
    return None


def _flag_nation(cell: Tag) -> str | None:
    image = cell.select_one("img[src*='/flags/']")
    if image and image.get("src"):
        match = re.search(r"/flags/([a-z]{3})\.", image["src"], re.I)
        if match:
            return match.group(1).upper()
    title = image.get("title") if image else None
    if title and re.fullmatch(r"[A-Z]{3}", title):
        return title
    return None


def _split_club_nation(raw: str) -> tuple[str, str | None]:
    match = CLUB_NATION_RE.fullmatch(raw)
    if match:
        return match.group(1).strip(), match.group(2)
    return raw, None


def _fold_metric(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _maybe_int(text: str) -> int | None:
    text = text.strip().rstrip(".")
    return int(text) if text.isdigit() else None


def _maybe_float(text: str) -> float | None:
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _maybe_metric(text: str) -> Any:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return None
    if re.fullmatch(r"-?\d+", compact):
        return int(compact)
    if re.fullmatch(r"-?\d+\.\d+", compact):
        return float(compact)
    return compact


def _direct_text(node: Tag) -> str:
    parts = []
    for child in node.children:
        if isinstance(child, Tag):
            continue
        text = str(child).strip()
        if text:
            parts.append(text)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
