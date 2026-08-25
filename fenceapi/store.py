from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fenceapi.models import (
    RankingCatalog,
    RankingCategory,
    RankingEntry,
    RankingFederation,
    RankingList,
)
from fenceapi.ranking_history import (
    RankingUpdate,
    diff_ranking_entries,
    iso_key,
    normalize_as_of,
    parse_calculated_on,
    ranking_content_hash,
)
from fenceapi.ranking_parsers import club_key, parse_club_mentions, resolve_federation
from fenceapi.urls import ranking_show_url

DEFAULT_DB_PATH = Path("data/rankings.sqlite")
DEFAULT_CLUBS_PATH = Path("data/clubs.json")
log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RankingStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
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
            CREATE TABLE IF NOT EXISTS ranking_snapshots (
              snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
              ranking_id INTEGER NOT NULL,
              calculated_on TEXT,
              calculated_at TEXT,
              fetched_at TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              athlete_count INTEGER
            );
            CREATE TABLE IF NOT EXISTS ranking_entry_history (
              snapshot_id INTEGER NOT NULL,
              ranking_id INTEGER NOT NULL,
              rank INTEGER,
              athlete_id INTEGER,
              name TEXT,
              nation TEXT,
              clubs_raw TEXT,
              yob INTEGER,
              points REAL
            );
            CREATE INDEX IF NOT EXISTS idx_entries_ranking ON ranking_entries(ranking_id);
            CREATE INDEX IF NOT EXISTS idx_clubs_name ON clubs(name);
            CREATE INDEX IF NOT EXISTS idx_snapshots_ranking ON ranking_snapshots(ranking_id);
            CREATE INDEX IF NOT EXISTS idx_history_athlete ON ranking_entry_history(athlete_id);
            CREATE INDEX IF NOT EXISTS idx_history_snapshot ON ranking_entry_history(snapshot_id);
            """
        )
        self._ensure_column("ranking_lists", "content_hash", "TEXT")
        self._migrate_snapshot_unique()
        self._backfill_snapshots()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    def _migrate_snapshot_unique(self) -> None:
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ranking_snapshots'"
        ).fetchone()
        if row is None or not row["sql"] or "UNIQUE" not in row["sql"].upper():
            return
        self._conn.executescript(
            """
            CREATE TABLE ranking_snapshots_new (
              snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
              ranking_id INTEGER NOT NULL,
              calculated_on TEXT,
              calculated_at TEXT,
              fetched_at TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              athlete_count INTEGER
            );
            INSERT INTO ranking_snapshots_new(
              snapshot_id, ranking_id, calculated_on, calculated_at, fetched_at, content_hash, athlete_count
            )
            SELECT snapshot_id, ranking_id, calculated_on, calculated_at, fetched_at, content_hash, athlete_count
            FROM ranking_snapshots;
            DROP TABLE ranking_snapshots;
            ALTER TABLE ranking_snapshots_new RENAME TO ranking_snapshots;
            CREATE INDEX IF NOT EXISTS idx_snapshots_ranking ON ranking_snapshots(ranking_id);
            """
        )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _backfill_snapshots(self) -> None:
        missing = self._conn.execute(
            """
            SELECT ranking_id FROM ranking_lists
            WHERE ranking_id NOT IN (SELECT DISTINCT ranking_id FROM ranking_snapshots)
            """
        ).fetchall()
        if not missing:
            return
        for row in missing:
            try:
                listing = self.get_ranking(row["ranking_id"])
                if listing is None:
                    continue
                fetched = self._conn.execute(
                    "SELECT fetched_at, content_hash FROM ranking_lists WHERE ranking_id=?",
                    (listing.ranking_id,),
                ).fetchone()
                fetched_at = fetched["fetched_at"] if fetched else utcnow()
                digest = ranking_content_hash(listing.entries)
                with self._lock:
                    with self._transaction():
                        if fetched and not fetched["content_hash"]:
                            self._conn.execute(
                                "UPDATE ranking_lists SET content_hash=? WHERE ranking_id=?",
                                (digest, listing.ranking_id),
                            )
                        self._insert_snapshot(listing, digest, fetched_at)
            except Exception:
                log.exception("Snapshot backfill failed for ranking %s", row["ranking_id"])

    def upsert_federation(self, federation: RankingFederation) -> None:
        with self._lock:
            with self._transaction():
                self._conn.execute(
                    """
                    INSERT INTO federations(id, name, level, nation, url)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      name=excluded.name, level=excluded.level, nation=excluded.nation, url=excluded.url
                    """,
                    (federation.id, federation.name, federation.level, federation.nation, federation.url),
                )

    def save_catalog(self, catalog: RankingCatalog) -> None:
        self.upsert_federation(catalog.federation)
        season = catalog.season or 0
        if not catalog.categories:
            existing = self._conn.execute(
                "SELECT 1 FROM catalogs WHERE federation_id=? AND season=? LIMIT 1",
                (catalog.federation.id, season),
            ).fetchone()
            if existing:
                log.warning(
                    "Keeping stored catalog for federation %s season %s; upstream returned no lists",
                    catalog.federation.id,
                    season,
                )
                return
        with self._lock:
            with self._transaction():
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

    def has_ranking(self, ranking_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM ranking_lists WHERE ranking_id=?",
            (ranking_id,),
        ).fetchone()
        return row is not None

    def latest_snapshot(self, ranking_id: int) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT snapshot_id, ranking_id, calculated_on, calculated_at, fetched_at,
                   content_hash, athlete_count
            FROM ranking_snapshots
            WHERE ranking_id=?
            ORDER BY COALESCE(calculated_at, fetched_at) DESC, snapshot_id DESC
            LIMIT 1
            """,
            (ranking_id,),
        ).fetchone()

    def save_ranking(
        self,
        ranking: RankingList,
        federation: RankingFederation | None = None,
        default_nation: str | None = None,
        ingest_clubs: bool = True,
    ) -> RankingUpdate:
        digest = ranking_content_hash(ranking.entries)
        previous = self.latest_snapshot(ranking.ranking_id)
        created = previous is None
        if previous is not None and previous["content_hash"] == digest:
            with self._lock:
                with self._transaction():
                    self._conn.execute(
                        "UPDATE ranking_lists SET fetched_at=? WHERE ranking_id=?",
                        (utcnow(), ranking.ranking_id),
                    )
            return RankingUpdate(
                ranking_id=ranking.ranking_id,
                unchanged=True,
                created=False,
                calculated_on=ranking.calculated_on,
                snapshot_id=previous["snapshot_id"],
            )

        previous_entries = [] if created else self._live_entries(ranking.ranking_id)
        if previous_entries and not ranking.entries:
            raise ValueError(
                f"Refusing to replace ranking {ranking.ranking_id} with an empty list"
            )
        added, removed, club_moves = diff_ranking_entries(previous_entries, ranking.entries)
        fetched = utcnow()
        nation = default_nation or (federation.nation if federation else None)
        with self._lock:
            with self._transaction():
                snapshot_id = self._insert_snapshot(ranking, digest, fetched)
                self._write_live_ranking(ranking, digest, fetched)
                if ingest_clubs:
                    self._ingest_clubs(ranking, nation)
        return RankingUpdate(
            ranking_id=ranking.ranking_id,
            unchanged=False,
            created=created,
            calculated_on=ranking.calculated_on,
            added=added,
            removed=removed,
            club_moves=club_moves,
            snapshot_id=snapshot_id,
        )

    def _write_live_ranking(self, ranking: RankingList, digest: str, fetched: str) -> None:
        self._conn.execute("DELETE FROM ranking_entries WHERE ranking_id=?", (ranking.ranking_id,))
        self._conn.execute(
            """
            INSERT INTO ranking_lists(
              ranking_id, title, weapon, gender, age_class, kind, season, calculated_on,
              athlete_count, fetched_at, content_hash
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ranking_id) DO UPDATE SET
              title=excluded.title,
              weapon=excluded.weapon,
              gender=excluded.gender,
              age_class=excluded.age_class,
              kind=excluded.kind,
              season=excluded.season,
              calculated_on=excluded.calculated_on,
              athlete_count=excluded.athlete_count,
              fetched_at=excluded.fetched_at,
              content_hash=excluded.content_hash
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
                digest,
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

    def _insert_snapshot(self, ranking: RankingList, digest: str, fetched_at: str) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO ranking_snapshots(
              ranking_id, calculated_on, calculated_at, fetched_at, content_hash, athlete_count
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                ranking.ranking_id,
                ranking.calculated_on,
                parse_calculated_on(ranking.calculated_on),
                fetched_at,
                digest,
                len(ranking.entries),
            ),
        )
        snapshot_id = int(cursor.lastrowid)
        self._conn.executemany(
            """
            INSERT INTO ranking_entry_history(
              snapshot_id, ranking_id, rank, athlete_id, name, nation, clubs_raw, yob, points
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    snapshot_id,
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
        return snapshot_id


    def cleanup_year_clubs(self) -> None:
        with self._lock:
            with self._transaction():
                self._conn.execute(
                    """
                    UPDATE ranking_entries
                    SET yob = COALESCE(yob, CAST(clubs_raw AS INTEGER)), clubs_raw = NULL
                    WHERE clubs_raw GLOB '[0-9][0-9][0-9][0-9]'
                    """
                )

    def rebuild_clubs(self) -> None:
        rows = self._conn.execute(
            "SELECT ranking_id, athlete_id, name, nation, clubs_raw, yob, points, rank FROM ranking_entries"
        ).fetchall()
        clubs: dict[str, dict[str, Any]] = {}
        for row in rows:
            for mention in parse_club_mentions(row["clubs_raw"]):
                key = club_key(mention.name)
                current = clubs.get(key)
                if current is None:
                    current = {
                        "key": key,
                        "name": mention.name,
                        "regions": set(),
                        "nations": set(),
                        "raws": set(),
                        "athletes": set(),
                        "appearances": 0,
                    }
                    clubs[key] = current
                current["appearances"] += 1
                if mention.region:
                    current["regions"].add(mention.region)
                if row["nation"]:
                    current["nations"].add(row["nation"])
                current["raws"].add(mention.raw)
                if row["athlete_id"]:
                    current["athletes"].add(int(row["athlete_id"]))
        payload = [
            (
                item["key"],
                item["name"],
                json.dumps(sorted(item["regions"]), ensure_ascii=False),
                json.dumps(sorted(item["nations"]), ensure_ascii=False),
                json.dumps(sorted(item["raws"])[:8], ensure_ascii=False),
                json.dumps(sorted(item["athletes"]), ensure_ascii=False),
                item["appearances"],
                utcnow(),
            )
            for item in clubs.values()
        ]
        with self._lock:
            with self._transaction():
                self._write_clubs(payload)

    def _write_clubs(self, payload: list[tuple[Any, ...]]) -> None:
        self._conn.execute("DELETE FROM clubs")
        self._conn.executemany(
            """
            INSERT INTO clubs(key, name, regions, nations, raw_examples, athlete_ids, appearances, updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            payload,
        )

    def _ingest_clubs(self, ranking: RankingList, nation: str | None) -> None:
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

    def get_ranking(self, ranking_id: int, as_of: str | None = None) -> RankingList | None:
        if as_of is None:
            row = self._conn.execute(
                """
                SELECT ranking_id, title, weapon, gender, age_class, kind, season, calculated_on
                FROM ranking_lists WHERE ranking_id=?
                """,
                (ranking_id,),
            ).fetchone()
            if row is None:
                return None
            entries = self._live_entries(ranking_id)
            return self._listing_from_row(row, entries)

        cutoff = iso_key(normalize_as_of(as_of))
        snapshots = self._conn.execute(
            """
            SELECT snapshot_id, ranking_id, calculated_on, calculated_at, fetched_at
            FROM ranking_snapshots
            WHERE ranking_id=?
            """,
            (ranking_id,),
        ).fetchall()
        eligible = [
            row
            for row in snapshots
            if iso_key(row["calculated_at"] or row["fetched_at"]) <= cutoff
        ]
        if not eligible:
            return None
        eligible.sort(
            key=lambda row: (
                iso_key(row["calculated_at"] or row["fetched_at"]),
                row["snapshot_id"],
            )
        )
        snapshot = eligible[-1]
        meta = self._conn.execute(
            """
            SELECT ranking_id, title, weapon, gender, age_class, kind, season, calculated_on
            FROM ranking_lists WHERE ranking_id=?
            """,
            (ranking_id,),
        ).fetchone()
        if meta is None:
            return None
        entries = self._history_entries(snapshot["snapshot_id"])
        listing = self._listing_from_row(meta, entries)
        listing.calculated_on = snapshot["calculated_on"]
        return listing

    def _listing_from_row(self, row: sqlite3.Row, entries: list[RankingEntry]) -> RankingList:
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

    def _live_entries(self, ranking_id: int) -> list[RankingEntry]:
        return [
            self._entry_from_row(item)
            for item in self._conn.execute(
                """
                SELECT rank, athlete_id, name, nation, clubs_raw, yob, points
                FROM ranking_entries WHERE ranking_id=?
                ORDER BY rank IS NULL, rank, name
                """,
                (ranking_id,),
            )
        ]

    def _history_entries(self, snapshot_id: int) -> list[RankingEntry]:
        return [
            self._entry_from_row(item)
            for item in self._conn.execute(
                """
                SELECT rank, athlete_id, name, nation, clubs_raw, yob, points
                FROM ranking_entry_history WHERE snapshot_id=?
                ORDER BY rank IS NULL, rank, name
                """,
                (snapshot_id,),
            )
        ]

    def _entry_from_row(self, item: sqlite3.Row) -> RankingEntry:
        return RankingEntry(
            rank=item["rank"],
            points=item["points"],
            transferred_points=None,
            name=item["name"] or "",
            athlete_id=item["athlete_id"],
            nation=item["nation"],
            clubs=item["clubs_raw"],
            yob=item["yob"],
        )

    def list_snapshots(self, ranking_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT snapshot_id, calculated_on, calculated_at, fetched_at, athlete_count
            FROM ranking_snapshots
            WHERE ranking_id=?
            ORDER BY COALESCE(calculated_at, fetched_at), snapshot_id
            """,
            (ranking_id,),
        ).fetchall()
        return [
            {
                "snapshot_id": row["snapshot_id"],
                "calculated_on": row["calculated_on"],
                "at": row["calculated_at"] or row["fetched_at"],
                "fetched_at": row["fetched_at"],
                "athletes": row["athlete_count"],
            }
            for row in rows
        ]

    def athlete_history(self, athlete_id: int) -> dict[str, Any] | None:
        rows = self._conn.execute(
            """
            SELECT
              h.athlete_id, h.name, h.nation, h.clubs_raw, h.rank, h.points, h.yob,
              s.snapshot_id, s.ranking_id, s.calculated_on, s.calculated_at, s.fetched_at,
              l.title, l.weapon, l.gender, l.age_class, l.kind, l.season,
              f.id AS federation_id, f.name AS federation_name, f.nation AS federation_nation,
              f.level AS federation_level
            FROM ranking_entry_history h
            JOIN ranking_snapshots s ON s.snapshot_id = h.snapshot_id
            JOIN ranking_lists l ON l.ranking_id = s.ranking_id
            LEFT JOIN catalogs c
              ON c.ranking_id = s.ranking_id
             AND (l.season IS NULL OR c.season = l.season)
            LEFT JOIN federations f ON f.id = c.federation_id
            WHERE h.athlete_id=?
            ORDER BY COALESCE(s.calculated_at, s.fetched_at), s.snapshot_id, l.weapon, l.age_class
            """,
            (athlete_id,),
        ).fetchall()
        if not rows:
            return None
        latest = rows[-1]
        rankings: list[dict[str, Any]] = []
        for row in rows:
            at = row["calculated_at"] or row["fetched_at"]
            rankings.append(
                {
                    "at": at,
                    "calculated_on": row["calculated_on"],
                    "season": row["season"],
                    "federation": row["federation_nation"] or row["federation_name"],
                    "federation_id": row["federation_id"],
                    "level": row["federation_level"],
                    "weapon": row["weapon"],
                    "gender": row["gender"],
                    "age": row["age_class"],
                    "kind": row["kind"],
                    "rank": row["rank"],
                    "points": row["points"],
                    "club": row["clubs_raw"],
                    "ranking_id": row["ranking_id"],
                    "title": row["title"],
                }
            )
        return {
            "athlete_id": athlete_id,
            "name": latest["name"],
            "nation": latest["nation"],
            "yob": latest["yob"],
            "clubs": _club_periods(rows),
            "rankings": rankings,
        }

    def stats(self) -> dict[str, Any]:
        def count(table: str) -> int:
            return int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        return {
            "db": str(self.path),
            "federations": count("federations"),
            "catalog_rows": count("catalogs"),
            "lists": count("ranking_lists"),
            "entries": count("ranking_entries"),
            "snapshots": count("ranking_snapshots"),
            "history_entries": count("ranking_entry_history"),
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


def _club_periods(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    periods: dict[str, dict[str, Any]] = {}
    last_at: str | None = None
    for row in rows:
        at = row["calculated_at"] or row["fetched_at"]
        last_at = at
        for mention in parse_club_mentions(row["clubs_raw"]):
            key = club_key(mention.name)
            current = periods.get(key)
            if current is None:
                periods[key] = {
                    "name": mention.name,
                    "from": at,
                    "to": at,
                }
            else:
                current["to"] = at
    return [
        {
            "name": item["name"],
            "from": item["from"],
            "to": None if item["to"] == last_at else item["to"],
        }
        for item in periods.values()
    ]
