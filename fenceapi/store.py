from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fenceapi.models import (
    RankingCatalog,
    RankingCategory,
    RankingEntry,
    RankingFederation,
    RankingList,
)
from fenceapi.ranking_parsers import club_key, parse_club_mentions, resolve_federation
from fenceapi.urls import ranking_show_url

DEFAULT_DB_PATH = Path("data/rankings.sqlite")
DEFAULT_CLUBS_PATH = Path("data/clubs.json")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RankingStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def close(self) -> None:
        self._conn.close()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS federations (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              level TEXT,
              nation TEXT,
              url TEXT
            );
            CREATE TABLE IF NOT EXISTS catalogs (
              federation_id INTEGER NOT NULL,
              season INTEGER NOT NULL,
              ranking_id INTEGER NOT NULL,
              group_name TEXT,
              weapon TEXT,
              gender TEXT,
              age_class TEXT,
              kind TEXT,
              PRIMARY KEY (federation_id, season, ranking_id)
            );
            CREATE TABLE IF NOT EXISTS ranking_lists (
              ranking_id INTEGER PRIMARY KEY,
              title TEXT,
              weapon TEXT,
              gender TEXT,
              age_class TEXT,
              kind TEXT,
              season INTEGER,
              calculated_on TEXT,
              athlete_count INTEGER,
              fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ranking_entries (
              ranking_id INTEGER NOT NULL,
              rank INTEGER,
              athlete_id INTEGER,
              name TEXT,
              nation TEXT,
              clubs_raw TEXT,
              yob INTEGER,
              points REAL
            );
            CREATE TABLE IF NOT EXISTS clubs (
              key TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              regions TEXT,
              nations TEXT,
              raw_examples TEXT,
              athlete_ids TEXT,
              appearances INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_entries_ranking ON ranking_entries(ranking_id);
            CREATE INDEX IF NOT EXISTS idx_clubs_name ON clubs(name);
            """
        )
        self._conn.commit()

    def upsert_federation(self, federation: RankingFederation) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO federations(id, name, level, nation, url)
                VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name, level=excluded.level, nation=excluded.nation, url=excluded.url
                """,
                (federation.id, federation.name, federation.level, federation.nation, federation.url),
            )
            self._conn.commit()

    def save_catalog(self, catalog: RankingCatalog) -> None:
        self.upsert_federation(catalog.federation)
        season = catalog.season or 0
        with self._lock:
            self._conn.execute(
                "DELETE FROM catalogs WHERE federation_id=? AND season=?",
                (catalog.federation.id, season),
            )
            self._conn.executemany(
                """
                INSERT INTO catalogs(
                  federation_id, season, ranking_id, group_name, weapon, gender, age_class, kind
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        catalog.federation.id,
                        season,
                        item.ranking_id,
                        item.group,
                        item.weapon,
                        item.gender,
                        item.age_class,
                        item.kind,
                    )
                    for item in catalog.categories
                ],
            )
            self._conn.commit()

    def has_ranking(self, ranking_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM ranking_lists WHERE ranking_id=?",
            (ranking_id,),
        ).fetchone()
        return row is not None

    def save_ranking(
        self,
        ranking: RankingList,
        federation: RankingFederation | None = None,
        default_nation: str | None = None,
    ) -> None:
        fetched = utcnow()
        with self._lock:
            self._conn.execute("DELETE FROM ranking_entries WHERE ranking_id=?", (ranking.ranking_id,))
            self._conn.execute(
                """
                INSERT INTO ranking_lists(
                  ranking_id, title, weapon, gender, age_class, kind, season, calculated_on, athlete_count, fetched_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ranking_id) DO UPDATE SET
                  title=excluded.title,
                  weapon=excluded.weapon,
                  gender=excluded.gender,
                  age_class=excluded.age_class,
                  kind=excluded.kind,
                  season=excluded.season,
                  calculated_on=excluded.calculated_on,
                  athlete_count=excluded.athlete_count,
                  fetched_at=excluded.fetched_at
                """,
                (
                    ranking.ranking_id,
                    ranking.title,
                    ranking.weapon,
                    ranking.gender,
                    ranking.age_class,
                    ranking.kind,
                    ranking.season,
                    ranking.calculated_on,
                    len(ranking.entries),
                    fetched,
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO ranking_entries(
                  ranking_id, rank, athlete_id, name, nation, clubs_raw, yob, points
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        ranking.ranking_id,
                        entry.rank,
                        entry.athlete_id,
                        entry.name,
                        entry.nation,
                        entry.clubs,
                        entry.yob,
                        entry.points,
                    )
                    for entry in ranking.entries
                ],
            )
            self._conn.commit()
        nation = default_nation or (federation.nation if federation else None)
        self._ingest_clubs(ranking, nation)

    def cleanup_year_clubs(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE ranking_entries
                SET yob = COALESCE(yob, CAST(clubs_raw AS INTEGER)), clubs_raw = NULL
                WHERE clubs_raw GLOB '[0-9][0-9][0-9][0-9]'
                """
            )
            self._conn.commit()

    def rebuild_clubs(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM clubs")
            self._conn.commit()
        rows = self._conn.execute(
            "SELECT ranking_id, athlete_id, name, nation, clubs_raw, yob, points, rank FROM ranking_entries"
        ).fetchall()
        from fenceapi.models import RankingEntry, RankingList

        by_list: dict[int, list[RankingEntry]] = {}
        for row in rows:
            by_list.setdefault(row["ranking_id"], []).append(
                RankingEntry(
                    rank=row["rank"],
                    points=row["points"],
                    transferred_points=None,
                    name=row["name"] or "",
                    athlete_id=row["athlete_id"],
                    nation=row["nation"],
                    clubs=row["clubs_raw"],
                    yob=row["yob"],
                )
            )
        for ranking_id, entries in by_list.items():
            listing = RankingList(
                ranking_id=ranking_id,
                url="",
                title="",
                weapon=None,
                gender=None,
                age_class=None,
                kind=None,
                calculated_on=None,
                season=None,
                entries=entries,
            )
            self._ingest_clubs(listing, None)

    def _ingest_clubs(self, ranking: RankingList, nation: str | None) -> None:
        with self._lock:
            for entry in ranking.entries:
                for mention in parse_club_mentions(entry.clubs):
                    key = club_key(mention.name)
                    row = self._conn.execute(
                        "SELECT regions, nations, raw_examples, athlete_ids, appearances FROM clubs WHERE key=?",
                        (key,),
                    ).fetchone()
                    if row:
                        regions = _json_set(row["regions"])
                        nations = _json_set(row["nations"])
                        raws = _json_set(row["raw_examples"])
                        athletes = _json_set(row["athlete_ids"], as_int=True)
                        appearances = int(row["appearances"]) + 1
                    else:
                        regions, nations, raws, athletes = set(), set(), set(), set()
                        appearances = 1
                    if mention.region:
                        regions.add(mention.region)
                    if entry.nation:
                        nations.add(entry.nation)
                    elif nation and len(nation) == 3:
                        nations.add(nation)
                    raws.add(mention.raw)
                    if entry.athlete_id:
                        athletes.add(entry.athlete_id)
                    self._conn.execute(
                        """
                        INSERT INTO clubs(key, name, regions, nations, raw_examples, athlete_ids, appearances, updated_at)
                        VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(key) DO UPDATE SET
                          name=excluded.name,
                          regions=excluded.regions,
                          nations=excluded.nations,
                          raw_examples=excluded.raw_examples,
                          athlete_ids=excluded.athlete_ids,
                          appearances=excluded.appearances,
                          updated_at=excluded.updated_at
                        """,
                        (
                            key,
                            mention.name,
                            json.dumps(sorted(regions), ensure_ascii=False),
                            json.dumps(sorted(nations), ensure_ascii=False),
                            json.dumps(sorted(raws)[:8], ensure_ascii=False),
                            json.dumps(sorted(athletes), ensure_ascii=False),
                            appearances,
                            utcnow(),
                        ),
                    )
            self._conn.commit()

    def list_clubs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT name, regions, nations, raw_examples, athlete_ids, appearances, updated_at FROM clubs ORDER BY name COLLATE NOCASE"
        ).fetchall()
        clubs: list[dict[str, Any]] = []
        for row in rows:
            athlete_ids = _json_list(row["athlete_ids"])
            clubs.append(
                {
                    "name": row["name"],
                    "regions": _json_list(row["regions"]),
                    "nations": _json_list(row["nations"]),
                    "athletes": len(athlete_ids),
                    "appearances": row["appearances"],
                    "examples": _json_list(row["raw_examples"]),
                    "updated_at": row["updated_at"],
                }
            )
        return clubs

    def export_clubs(self, path: str | Path | None = None) -> Path:
        destination = Path(path) if path else DEFAULT_CLUBS_PATH
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": utcnow(),
            "count": 0,
            "clubs": self.list_clubs(),
        }
        payload["count"] = len(payload["clubs"])
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(destination)
        return destination

    def list_federations(self) -> list[RankingFederation]:
        rows = self._conn.execute(
            "SELECT id, name, level, nation, url FROM federations ORDER BY level, name"
        ).fetchall()
        return [
            RankingFederation(
                id=row["id"],
                name=row["name"],
                level=row["level"] or "",
                nation=row["nation"],
                url=row["url"] or "",
            )
            for row in rows
        ]

    def get_federation(self, spec: str) -> RankingFederation:
        return resolve_federation(self.list_federations(), spec)

    def catalog_seasons(self, federation_id: int) -> list[int]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT season FROM catalogs
            WHERE federation_id=? AND season > 0
            ORDER BY season DESC
            """,
            (federation_id,),
        ).fetchall()
        return [int(row["season"]) for row in rows]

    def get_catalog(
        self,
        federation: str | int | RankingFederation,
        season: int | None = None,
    ) -> RankingCatalog | None:
        resolved = federation if isinstance(federation, RankingFederation) else self.get_federation(str(federation))
        seasons = self.catalog_seasons(resolved.id)
        if season is None:
            season = seasons[0] if seasons else None
        if season is None:
            return None
        rows = self._conn.execute(
            """
            SELECT ranking_id, group_name, weapon, gender, age_class, kind
            FROM catalogs
            WHERE federation_id=? AND season=?
            ORDER BY group_name, weapon, gender, age_class
            """,
            (resolved.id, season),
        ).fetchall()
        if not rows:
            return None
        categories = [
            RankingCategory(
                ranking_id=row["ranking_id"],
                url=ranking_show_url(row["ranking_id"]),
                group=row["group_name"] or "",
                weapon=row["weapon"] or "",
                gender=row["gender"] or "",
                age_class=row["age_class"] or "",
                kind=row["kind"] or "individual",
            )
            for row in rows
        ]
        return RankingCatalog(
            federation=resolved,
            season=season,
            title=resolved.name,
            url=resolved.url,
            categories=categories,
            seasons=seasons,
        )

    def get_ranking(self, ranking_id: int) -> RankingList | None:
        row = self._conn.execute(
            """
            SELECT ranking_id, title, weapon, gender, age_class, kind, season, calculated_on
            FROM ranking_lists WHERE ranking_id=?
            """,
            (ranking_id,),
        ).fetchone()
        if row is None:
            return None
        entries = [
            RankingEntry(
                rank=item["rank"],
                points=item["points"],
                transferred_points=None,
                name=item["name"] or "",
                athlete_id=item["athlete_id"],
                nation=item["nation"],
                clubs=item["clubs_raw"],
                yob=item["yob"],
            )
            for item in self._conn.execute(
                """
                SELECT rank, athlete_id, name, nation, clubs_raw, yob, points
                FROM ranking_entries WHERE ranking_id=?
                ORDER BY rank IS NULL, rank, name
                """,
                (ranking_id,),
            )
        ]
        return RankingList(
            ranking_id=row["ranking_id"],
            url=ranking_show_url(row["ranking_id"]),
            title=row["title"] or "",
            weapon=row["weapon"],
            gender=row["gender"],
            age_class=row["age_class"],
            kind=row["kind"],
            calculated_on=row["calculated_on"],
            season=row["season"],
            entries=entries,
        )

    def stats(self) -> dict[str, Any]:
        def count(table: str) -> int:
            return int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        return {
            "db": str(self.path),
            "federations": count("federations"),
            "catalog_rows": count("catalogs"),
            "lists": count("ranking_lists"),
            "entries": count("ranking_entries"),
            "clubs": count("clubs"),
        }


def _json_set(value: str | None, as_int: bool = False) -> set[Any]:
    items = _json_list(value)
    if as_int:
        return {int(item) for item in items}
    return set(items)


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
