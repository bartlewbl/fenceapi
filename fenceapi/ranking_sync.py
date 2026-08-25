from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, TextIO

from fenceapi.models import RankingFederation
from fenceapi.scraper import Scraper
from fenceapi.store import DEFAULT_CLUBS_PATH, RankingStore, utcnow

log = logging.getLogger(__name__)


@dataclass
class RankingSyncReport:
    scraped_at: str
    federations: int = 0
    catalogs: int = 0
    lists_fetched: int = 0
    lists_updated: int = 0
    lists_unchanged: int = 0
    lists_skipped: int = 0
    entries: int = 0
    clubs: int = 0
    clubs_path: str | None = None
    athletes_added: int = 0
    athletes_removed: int = 0
    club_moves: int = 0
    changes: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scraped_at": self.scraped_at,
            "federations": self.federations,
            "catalogs": self.catalogs,
            "lists_fetched": self.lists_fetched,
            "lists_updated": self.lists_updated,
            "lists_unchanged": self.lists_unchanged,
            "lists_skipped": self.lists_skipped,
            "entries": self.entries,
            "clubs": self.clubs,
            "clubs_path": self.clubs_path,
            "athletes_added": self.athletes_added,
            "athletes_removed": self.athletes_removed,
            "club_moves": self.club_moves,
            "changes": self.changes,
            "errors": self.errors,
        }


class RankingSyncer:
    def __init__(
        self,
        scraper: Scraper,
        store: RankingStore,
        clubs_path: str | Path | None = None,
        progress: TextIO | None = sys.stderr,
    ) -> None:
        self.scraper = scraper
        self.store = store
        self.clubs_path = Path(clubs_path) if clubs_path else DEFAULT_CLUBS_PATH
        self.progress = progress

    def run(
        self,
        federations: Iterable[str | int | RankingFederation] | None = None,
        all_seasons: bool = True,
        season: int | None = None,
        weapon: str | None = None,
        gender: str | None = None,
        age: str | None = None,
        kind: str | None = None,
        group: str | None = None,
        refresh_current: bool = False,
    ) -> RankingSyncReport:
        from fenceapi.ranking_parsers import filter_categories, resolve_federation

        report = RankingSyncReport(scraped_at=utcnow())
        self.store.cleanup_year_clubs()
        known = self.scraper.ranking_federations()
        if federations is None:
            targets = known
        else:
            targets = []
            for item in federations:
                if isinstance(item, RankingFederation):
                    targets.append(item)
                else:
                    targets.append(resolve_federation(known, str(item)))

        try:
            for index, federation in enumerate(targets, start=1):
                self._log(f"[{index}/{len(targets)}] {federation.nation or ''} {federation.name} ({federation.level})")
                self.store.upsert_federation(federation)
                report.federations += 1
                try:
                    current = self.scraper.ranking_catalog(federation)
                except Exception as exc:
                    report.errors.append(f"catalog {federation.id}: {exc}")
                    log.exception("Catalog failed for %s", federation.id)
                    continue
                if season:
                    seasons = [season]
                elif all_seasons:
                    seasons = current.seasons or ([current.season] if current.season else [])
                else:
                    seasons = [current.season] if current.season else (current.seasons[:1] if current.seasons else [])
                for year in [item for item in seasons if item]:
                    try:
                        catalog = current if year == current.season else self.scraper.ranking_catalog(federation, year)
                        if catalog.categories:
                            self.store.save_catalog(catalog)
                        else:
                            stored = self.store.get_catalog(federation, season=year)
                            if stored is None:
                                raise ValueError("upstream catalog was empty")
                            catalog = stored
                            self._log(f"  season {year}: kept stored catalog; upstream had no lists")
                    except Exception as exc:
                        report.errors.append(f"catalog {federation.id} {year}: {exc}")
                        continue
                    report.catalogs += 1
                    categories = filter_categories(
                        catalog.categories,
                        weapon=weapon,
                        gender=gender,
                        age=age,
                        kind=kind,
                        group=group,
                    )
                    self._log(f"  season {year}: {len(categories)} lists")
                    for category in categories:
                        existed = self.store.has_ranking(category.ranking_id)
                        if existed and not refresh_current:
                            report.lists_skipped += 1
                            continue
                        try:
                            listing = self.scraper.ranking(category.ranking_id)
                            update = self.store.save_ranking(
                                listing, federation=federation, ingest_clubs=False
                            )
                        except Exception as exc:
                            report.errors.append(f"list {category.ranking_id}: {exc}")
                            log.exception("Ranking list %s failed", category.ranking_id)
                            continue
                        if update.unchanged:
                            report.lists_unchanged += 1
                            continue
                        report.entries += len(listing.entries)
                        if update.created:
                            report.lists_fetched += 1
                        else:
                            report.lists_updated += 1
                            report.athletes_added += len(update.added)
                            report.athletes_removed += len(update.removed)
                            report.club_moves += len(update.club_moves)
                            report.changes.append(update.to_dict())
                            self._log(
                                f"  {listing.weapon} {listing.gender} {listing.age_class}: "
                                f"{listing.calculated_on or 'updated'} "
                                f"+{len(update.added)} -{len(update.removed)} "
                                f"{len(update.club_moves)} club moves"
                            )
                        if (report.lists_fetched + report.lists_updated) % 10 == 0:
                            self._export(report)
        except KeyboardInterrupt:
            report.errors.append("interrupted")
            self._log("interrupted; writing clubs so far")
        try:
            self.store.rebuild_clubs()
            self._export(report)
            report.clubs = self.store.stats()["clubs"]
        except Exception as exc:
            report.errors.append(f"clubs: {exc}")
            log.exception("Club rebuild/export failed")
        return report

    def _export(self, report: RankingSyncReport) -> None:
        try:
            path = self.store.export_clubs(self.clubs_path)
        except Exception as exc:
            report.errors.append(f"clubs export: {exc}")
            log.exception("Club export failed")
            return
        report.clubs_path = str(path)
        stats = self.store.stats()
        self._log(
            f"  saved {stats['lists']} lists, {stats['entries']} athletes, "
            f"{stats['snapshots']} snapshots, {stats['clubs']} clubs -> {path}"
        )

    def _log(self, message: str) -> None:
        if self.progress is not None:
            print(message, file=self.progress, flush=True)
