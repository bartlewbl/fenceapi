from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from fenceapi.models import RankingEntry

CALCULATED_ON_RE = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.(\d{4})\.?\s*(\d{1,2})(?::(\d{2}))?"
)
ISO_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?)?)?")


def parse_calculated_on(text: str | None) -> str | None:
    """Return an ISO datetime for Ophardt '24.07.2026. 11:59' stamps."""
    if not text:
        return None
    raw = text.strip()
    iso = ISO_DATE_RE.fullmatch(raw.replace("Z", "").strip())
    if iso:
        date, time = iso.group(1), iso.group(2)
        if not time:
            return f"{date}T00:00:00"
        if time.count(":") == 1:
            time = f"{time}:00"
        return f"{date}T{time}"
    match = CALCULATED_ON_RE.search(raw)
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    return (
        f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        f"T{hour.zfill(2)}:{(minute or '00').zfill(2)}:00"
    )


def normalize_as_of(value: str) -> str:
    """Inclusive cutoff: a date-only value means the end of that UTC day."""
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T23:59:59"
    parsed = parse_calculated_on(text)
    if parsed:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return f"{parsed[:10]}T23:59:59"
        return parsed
    raise ValueError(f"Invalid as_of date {value!r}. Use YYYY-MM-DD.")


def iso_key(value: str | None) -> str:
    """Comparable YYYY-MM-DDTHH:MM:SS prefix, ignoring timezone and fractions."""
    if not value:
        return ""
    text = value.strip().replace(" ", "T").replace("Z", "")
    for sep in ("+", "-"):
        # Timezone offset after the date: 2026-07-24T11:59:00+00:00
        if sep == "-" and text.count("-") <= 2:
            continue
        idx = text.find(sep, 19) if len(text) > 19 else -1
        if idx > 0:
            text = text[:idx]
            break
    return text[:19]


def ranking_content_hash(entries: list[RankingEntry]) -> str:
    parts: list[str] = []
    ordered = sorted(
        entries,
        key=lambda item: (item.athlete_id or 0, item.rank or 0, item.name, item.clubs or ""),
    )
    for item in ordered:
        points = "" if item.points is None else str(item.points)
        parts.append(
            f"{item.athlete_id}|{item.rank}|{points}|{item.clubs or ''}|{item.nation or ''}"
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def entry_identity(entry: RankingEntry) -> str:
    if entry.athlete_id is not None:
        return f"id:{entry.athlete_id}"
    return f"name:{(entry.name or '').casefold()}"


def diff_ranking_entries(
    previous: list[RankingEntry],
    current: list[RankingEntry],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    old_map = {entry_identity(item): item for item in previous}
    new_map = {entry_identity(item): item for item in current}
    added = [
        _entry_summary(item)
        for key, item in new_map.items()
        if key not in old_map
    ]
    removed = [
        _entry_summary(item)
        for key, item in old_map.items()
        if key not in new_map
    ]
    club_moves: list[dict[str, Any]] = []
    for key, item in new_map.items():
        prior = old_map.get(key)
        if prior is None:
            continue
        old_club = (prior.clubs or "").strip()
        new_club = (item.clubs or "").strip()
        if old_club != new_club:
            club_moves.append(
                {
                    **_entry_summary(item),
                    "from": prior.clubs,
                    "to": item.clubs,
                }
            )
    return added, removed, club_moves


def _entry_summary(entry: RankingEntry) -> dict[str, Any]:
    return {
        "athlete_id": entry.athlete_id,
        "name": entry.name,
        "rank": entry.rank,
        "club": entry.clubs,
    }


@dataclass
class RankingUpdate:
    ranking_id: int
    unchanged: bool
    created: bool
    calculated_on: str | None
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    club_moves: list[dict[str, Any]] = field(default_factory=list)
    snapshot_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ranking_id": self.ranking_id,
            "unchanged": self.unchanged,
            "created": self.created,
            "calculated_on": self.calculated_on,
            "added": self.added,
            "removed": self.removed,
            "club_moves": self.club_moves,
        }
        if self.snapshot_id is not None:
            payload["snapshot_id"] = self.snapshot_id
        return payload
