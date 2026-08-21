from __future__ import annotations

import re
import unicodedata

from bs4 import BeautifulSoup, Tag

from fenceapi.models import (
    ClubMention,
    RankingCatalog,
    RankingCategory,
    RankingEntry,
    RankingFederation,
    RankingList,
)
from fenceapi.urls import (
    BIOGRAPHY_RE,
    OPHARDT_BASE,
    RANKING_SHOW_RE,
    absolute,
    ranking_show_url,
)

WEAPON_ALIASES = {
    "epee": "epee",
    "épée": "epee",
    "degen": "epee",
    "e": "epee",
    "foil": "foil",
    "florett": "foil",
    "f": "foil",
    "sabre": "sabre",
    "saber": "sabre",
    "säbel": "sabre",
    "saebel": "sabre",
    "sabel": "sabre",
    "s": "sabre",
}
GENDER_ALIASES = {
    "men": "men",
    "mens": "men",
    "men's": "men",
    "male": "men",
    "m": "men",
    "herren": "men",
    "women": "women",
    "womens": "women",
    "women's": "women",
    "female": "women",
    "w": "women",
    "damen": "women",
    "mixed": "mixed",
    "open": "open",
}
KIND_ALIASES = {
    "individual": "individual",
    "ind": "individual",
    "einzel": "individual",
    "team": "team",
    "teams": "team",
    "mannschaft": "team",
}
LEVEL_NAMES = {"I": "international", "Z": "zonal", "N": "national", "R": "regional"}
SPECIAL_FEDERATIONS = {"fie": "FIE", "efc": "EFC", "cae": "CAE", "dfb": "GER"}


def parse_ranking_federations(html: str, url: str) -> list[RankingFederation]:
    soup = BeautifulSoup(html, "html.parser")
    federations: list[RankingFederation] = []
    seen: set[int] = set()
    for card in soup.select("div.card"):
        header = card.select_one("[class*=rankingoverview]")
        if header is None:
            continue
        classes = " ".join(header.get("class", []))
        level_code = "N"
        match = re.search(r"rankingoverview-([A-Z])", classes)
        if match:
            level_code = match.group(1)
        name = re.sub(r"\s+", " ", header.get_text(" ", strip=True))
        flag = card.select_one("img[src*='/flags/']")
        nation = None
        if flag and flag.get("src"):
            flag_match = re.search(r"/flags/([a-z]{3})\.svg", flag["src"], re.I)
            if flag_match:
                nation = flag_match.group(1).upper()
        if nation is None:
            lowered = name.lower()
            if "internationale d'escrime" in lowered or lowered.startswith("fie"):
                nation = "FIE"
            elif "european fencing" in lowered or "efc" == lowered:
                nation = "EFC"
            elif "africaine" in lowered:
                nation = "CAE"
        link = None
        for item in card.select("a[href*='/search/rankings/']"):
            href = item.get("href", "")
            if re.search(r"/search/rankings/\d+", href) and "show" not in href:
                text = item.get_text(" ", strip=True).lower()
                if "series" in text:
                    continue
                link = item
                if "ranking" in text:
                    break
        if link is None:
            continue
        id_match = re.search(r"/search/rankings/(\d+)", link["href"])
        if not id_match:
            continue
        fed_id = int(id_match.group(1))
        if fed_id in seen:
            continue
        seen.add(fed_id)
        federations.append(
            RankingFederation(
                id=fed_id,
                name=name,
                level=LEVEL_NAMES.get(level_code, level_code.lower()),
                nation=nation,
                url=absolute(link["href"], OPHARDT_BASE),
            )
        )
    return federations


def parse_ranking_catalog(html: str, url: str, federation: RankingFederation) -> RankingCatalog:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else federation.name
    season = _selected_season(soup)
    seasons = _available_seasons(soup)
    categories: list[RankingCategory] = []
    for card in soup.select("div.card"):
        heading = card.find("h4")
        table = card.find("table")
        if heading is None or table is None:
            continue
        group = heading.get_text(" ", strip=True)
        categories.extend(_parse_matrix(table, group, season))
    return RankingCatalog(
        federation=federation,
        season=season,
        title=title,
        url=url,
        categories=categories,
        seasons=seasons,
    )


def parse_ranking_list(html: str, url: str, ranking_id: int) -> RankingList:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.find("h1")
    title = title_el.get_text(" ", strip=True) if title_el else f"Ranking {ranking_id}"
    season = _season_from_title(title)
    meta = _parse_ranking_meta(soup)
    entries = _parse_ranking_entries(soup)
    return RankingList(
        ranking_id=ranking_id,
        url=url,
        title=title,
        weapon=meta.get("weapon"),
        gender=meta.get("gender"),
        age_class=meta.get("age_class"),
        kind=meta.get("kind"),
        calculated_on=meta.get("calculated_on"),
        season=season,
        entries=entries,
    )


def resolve_federation(federations: list[RankingFederation], query: str) -> RankingFederation:
    raw = query.strip()
    if raw.isdigit():
        fed_id = int(raw)
        for item in federations:
            if item.id == fed_id:
                return item
        raise ValueError(f"No ranking federation with id {fed_id}")

    wanted = _fold(raw)
    alias = SPECIAL_FEDERATIONS.get(wanted)
    if alias:
        wanted_nation = alias
    else:
        wanted_nation = wanted.upper() if len(wanted) <= 3 else None

    nationals = [item for item in federations if item.level == "national"]
    internationals = [item for item in federations if item.level in {"international", "zonal"}]
    pool = internationals + nationals + federations

    if wanted_nation:
        for item in pool:
            if (item.nation or "").upper() == wanted_nation.upper():
                return item
        if wanted_nation == "FIE":
            for item in federations:
                if "internationale d'escrime" in item.name.lower() or item.nation == "FIE":
                    return item

    matches = [item for item in federations if wanted in _fold(item.name)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        for item in matches:
            if item.level == "national":
                return item
        return matches[0]
    raise ValueError(
        f"Unknown federation {query!r}. Try ger, fie, efc, a NOC code, or an id from `rankings` with no args."
    )


def filter_categories(
    categories: list[RankingCategory],
    weapon: str | None = None,
    gender: str | None = None,
    age: str | None = None,
    kind: str | None = None,
    group: str | None = None,
) -> list[RankingCategory]:
    wanted_weapon = normalize_weapon(weapon) if weapon else None
    wanted_gender = normalize_gender(gender) if gender else None
    wanted_age = normalize_age(age) if age else None
    wanted_kind = normalize_kind(kind) if kind else None
    wanted_group = _fold(group) if group else None
    matches: list[RankingCategory] = []
    for item in categories:
        if wanted_weapon and item.weapon != wanted_weapon:
            continue
        if wanted_gender and item.gender != wanted_gender:
            continue
        if wanted_age and item.age_class != wanted_age:
            continue
        if wanted_kind and item.kind != wanted_kind:
            continue
        if wanted_group and wanted_group not in _fold(item.group):
            continue
        matches.append(item)
    return matches


def normalize_weapon(text: str) -> str:
    folded = _fold(text)
    if folded in WEAPON_ALIASES:
        return WEAPON_ALIASES[folded]
    raise ValueError(f"Unknown weapon {text!r}. Use epee, foil, or sabre.")


def normalize_gender(text: str) -> str:
    folded = _fold(text).replace("'", "")
    if folded in GENDER_ALIASES:
        return GENDER_ALIASES[folded]
    raise ValueError(f"Unknown gender {text!r}. Use men or women.")


def normalize_age(text: str) -> str:
    folded = _fold(text).replace("-", "").replace(" ", "")
    if folded in {"sen", "senioren"}:
        return "senior"
    if re.fullmatch(r"u\d{1,2}", folded) or re.fullmatch(r"v\d{2}", folded) or re.fullmatch(r"o\d{2}", folded):
        return folded
    if folded in {"veteran", "veterans", "senior"}:
        return folded if folded != "veterans" else "veteran"
    return folded


def normalize_kind(text: str) -> str:
    folded = _fold(text)
    if folded in KIND_ALIASES:
        return KIND_ALIASES[folded]
    return folded or "individual"


def _parse_matrix(table: Tag, group: str, season: int | None) -> list[RankingCategory]:
    header_rows = table.select("thead tr")
    if len(header_rows) < 2:
        return []
    genders: list[str] = []
    for th in header_rows[0].find_all("th")[1:]:
        label = normalize_gender(th.get_text(" ", strip=True) or "open")
        span = int(th.get("colspan") or 1)
        genders.extend([label] * span)
    weapons = [normalize_weapon(th.get_text(" ", strip=True)) for th in header_rows[1].find_all("th")]
    columns = list(zip(genders, weapons, strict=False))
    categories: list[RankingCategory] = []
    for row in table.select("tbody tr"):
        age_cell = row.find("th")
        if age_cell is None:
            continue
        age = normalize_age(age_cell.get_text(" ", strip=True))
        cells = row.find_all("td")
        for index, cell in enumerate(cells):
            if index >= len(columns):
                break
            gender, weapon = columns[index]
            for link in cell.find_all("a", href=True):
                id_match = RANKING_SHOW_RE.search(link["href"])
                if not id_match:
                    continue
                ranking_id = int(id_match.group(1))
                label = re.sub(r"\s+", " ", link.get_text(" ", strip=True))
                kind = "team" if "team" in label.lower() else "individual"
                categories.append(
                    RankingCategory(
                        ranking_id=ranking_id,
                        url=ranking_show_url(ranking_id),
                        group=group,
                        weapon=weapon,
                        gender=gender,
                        age_class=age,
                        kind=kind,
                        label=label,
                    )
                )
    return categories


def _parse_ranking_meta(soup: BeautifulSoup) -> dict[str, str | None]:
    meta: dict[str, str | None] = {
        "weapon": None,
        "gender": None,
        "age_class": None,
        "kind": None,
        "calculated_on": None,
    }
    table = soup.select_one("table")
    if table is None:
        return meta
    headers = [th.get_text(" ", strip=True).lower() for th in table.select("tr th")]
    first_data = table.select("tr")
    if len(first_data) < 2:
        return meta
    values = [td.get_text(" ", strip=True) for td in first_data[1].find_all("td")]
    mapping = dict(zip(headers, values, strict=False))
    if mapping.get("discipline"):
        try:
            meta["weapon"] = normalize_weapon(mapping["discipline"])
        except ValueError:
            meta["weapon"] = mapping["discipline"].lower()
    if mapping.get("gender"):
        try:
            meta["gender"] = normalize_gender(mapping["gender"])
        except ValueError:
            meta["gender"] = mapping["gender"].lower()
    if mapping.get("ageclass") or mapping.get("age class"):
        meta["age_class"] = normalize_age(mapping.get("ageclass") or mapping.get("age class") or "")
    if mapping.get("category"):
        meta["kind"] = normalize_kind(mapping["category"])
    meta["calculated_on"] = mapping.get("calculated on")
    return meta


def _parse_ranking_entries(soup: BeautifulSoup) -> list[RankingEntry]:
    table = soup.select_one("table.rankingbody")
    if table is None:
        return []
    entries: list[RankingEntry] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 4:
            continue
        if "ranking" not in (cells[0].get("class") or []):
            continue
        name_cell = cells[3]
        name_link = name_cell.find("a", href=BIOGRAPHY_RE)
        name_el = name_cell.select_one("a.dropdown-toggle") or name_link
        name = name_el.get_text(" ", strip=True) if name_el else name_cell.get_text(" ", strip=True)
        athlete_id = None
        bio = name_cell.find("a", href=BIOGRAPHY_RE)
        if bio:
            athlete_id = int(BIOGRAPHY_RE.search(bio["href"]).group(1))
        nation = None
        clubs = None
        yob = None
        if len(cells) > 4:
            nation_text = cells[4].get_text(" ", strip=True)
            nation_match = re.search(r"\b([A-Z]{3})\b", nation_text)
            nation = nation_match.group(1) if nation_match else None
        club_cell = row.find("td", class_=lambda value: value and "rankingclub" in value)
        if club_cell:
            clubs = club_cell.get_text(" ", strip=True) or None
        for cell in cells[4:]:
            text = cell.get_text(" ", strip=True)
            if re.fullmatch(r"(19|20)\d{2}", text):
                yob = int(text)
                break
        if clubs and re.fullmatch(r"(19|20)\d{2}", clubs.strip()):
            yob = yob or int(clubs)
            clubs = None
        entries.append(
            RankingEntry(
                rank=_maybe_int(cells[0].get_text(" ", strip=True)),
                points=_maybe_float(cells[1].get_text(" ", strip=True)),
                transferred_points=_maybe_float(cells[2].get_text(" ", strip=True)),
                name=name,
                athlete_id=athlete_id,
                nation=nation,
                clubs=clubs,
                yob=yob,
            )
        )
    return entries


CLUB_PREFIXES = {
    "fc", "tv", "sc", "sv", "tsv", "tsc", "usc", "vfl", "vfb", "fz", "fg", "sg",
    "tus", "dfc", "ofc", "bfc", "kfc", "wmt", "wmtv", "zfc", "fechtclub", "fechter",
}
REGION_RE = re.compile(r"^([A-ZÄÖÜ]{2,4})\s+(.+)$")


def parse_club_mentions(raw: str | None) -> list[ClubMention]:
    if not raw:
        return []
    mentions: list[ClubMention] = []
    seen: set[tuple[str, str | None]] = set()
    for chunk in _club_chunks(raw):
        region, name = _split_region(chunk)
        name = re.sub(r"\s+", " ", name).strip(" ,;")
        if len(name) < 2 or re.fullmatch(r"(19|20)\d{2}", name) or name.isdigit():
            continue
        key = (_fold(name), region)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(ClubMention(name=name, region=region, raw=chunk))
    return mentions


def _club_chunks(raw: str) -> list[str]:
    chunks: list[str] = []
    remainder = raw
    for inner in re.findall(r"\(([^)]+)\)", raw):
        chunks.append(inner.strip())
        remainder = remainder.replace(f"({inner})", " ")
    for part in remainder.split(","):
        text = part.strip(" ,;")
        if text:
            chunks.append(text)
    return chunks


def _split_region(text: str) -> tuple[str | None, str]:
    match = REGION_RE.match(text.strip())
    if not match:
        return None, text.strip()
    code, rest = match.group(1), match.group(2).strip()
    if _fold(code) in CLUB_PREFIXES:
        return None, text.strip()
    if len(rest) < 2:
        return None, text.strip()
    return code, rest


def _selected_season(soup: BeautifulSoup) -> int | None:
    selected = soup.select_one("select[name=season] option[selected]")
    if selected and selected.get("value", "").isdigit():
        return int(selected["value"])
    option = soup.select_one("select[name=season] option")
    if option and option.get("value", "").isdigit():
        return int(option["value"])
    return None


def _available_seasons(soup: BeautifulSoup) -> list[int]:
    years: list[int] = []
    for option in soup.select("select[name=season] option"):
        value = option.get("value", "")
        if value.isdigit():
            year = int(value)
            if year not in years:
                years.append(year)
    return years


def _season_from_title(title: str) -> int | None:
    match = re.search(r"(20\d{2})", title)
    return int(match.group(1)) if match else None


def club_key(name: str) -> str:
    return _fold(name)


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


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
