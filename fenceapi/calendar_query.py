from __future__ import annotations

CALENDAR_GROUPS = {
    "international": "I",
    "zonal": "Z",
    "national": "N",
    "regional": "R",
    "tournaments": "T",
    "other": "S",
}
CALENDAR_WEAPONS = {"epee": "E", "foil": "F", "sabre": "S", "e": "E", "f": "F", "s": "S"}
CALENDAR_GENDERS = {
    "men": "M",
    "mens": "M",
    "women": "F",
    "womens": "F",
    "open": "O",
    "mixed": "X",
}
CALENDAR_VENUES = {
    "tournament": "T",
    "tournaments": "T",
    "examination": "X",
    "courses": "C",
    "training": "E",
    "camp": "S",
    "assembly": "O",
    "celebration": "R",
}
CALENDAR_AGES = {
    "u8": "61",
    "u9": "60",
    "u10": "56",
    "u11": "55",
    "u12": "54",
    "u13": "45",
    "u14": "40",
    "u15": "35",
    "u16": "120",
    "u17": "30",
    "u18": "112",
    "u20": "20",
    "u23": "15",
    "senior": "10",
    "v40": "73",
    "v50": "74",
}


def calendar_group(value: str | None) -> str | None:
    if not value:
        return None
    return CALENDAR_GROUPS.get(value.lower(), value)


def calendar_discipline(value: str | None) -> str | None:
    if not value:
        return None
    return CALENDAR_WEAPONS.get(value.lower(), value.upper()[:1])


def calendar_gender(value: str | None) -> str | None:
    if not value:
        return None
    return CALENDAR_GENDERS.get(value.lower().replace("'", ""), value)


def calendar_ageclass(value: str | None) -> str | None:
    if not value:
        return None
    if value.isdigit():
        return value
    return CALENDAR_AGES.get(value.lower(), value)


def calendar_venue(value: str | None) -> str | None:
    if not value:
        return "T"
    return CALENDAR_VENUES.get(value.lower(), value.upper()[:1])


def calendar_filters(
    nation: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    region: str | None = None,
    city: str | None = None,
    title: str | None = None,
    event_type: str | None = None,
    weapon: str | None = None,
    gender: str | None = None,
    age: str | None = None,
    venue: str | None = "tournament",
) -> dict[str, str | None]:
    return {
        "nation": nation.upper() if nation else None,
        "date_from": date_from,
        "date_to": date_to,
        "region": region,
        "city": city,
        "title": title,
        "group": calendar_group(event_type),
        "discipline": calendar_discipline(weapon),
        "gender": calendar_gender(gender),
        "ageclass": calendar_ageclass(age),
        "venuetype": calendar_venue(venue),
    }
