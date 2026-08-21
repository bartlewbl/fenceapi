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
    lists_skipped: int = 0
    entries: int = 0
    clubs: int = 0
    clubs_path: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scraped_at": self.scraped_at,
            "federations": self.federations,
            "catalogs": self.catalogs,
            "lists_fetched": self.lists_fetched,
            "lists_skipped": self.lists_skipped,
            "entries": self.entries,
            "clubs": self.clubs,
            "clubs_path": self.clubs_path,
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
    ) -> RankingSyncReport:
        from fenceapi.ranking_parsers import filter_categories, resolve_federation

        report = RankingSyncReport(scraped_at=utcnow())
        self.store.cleanup_year_clubs()
        self.store.rebuild_clubs()
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
                seasons = [season] if season else (current.seasons or [current.season])
                if not all_seasons:
                    seasons = [current.season or (seasons[0] if seasons else None)]
                for year in [item for item in seasons if item]:
                    try:
                        catalog = current if year == current.season else self.scraper.ranking_catalog(federation, year)
                    except Exception as exc:
                        report.errors.append(f"catalog {federation.id} {year}: {exc}")
                        continue
                    self.store.save_catalog(catalog)
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
                        if self.store.has_ranking(category.ranking_id):
                            report.lists_skipped += 1
                            continue
                        try:
                            listing = self.scraper.ranking(category.ranking_id)
                        except Exception as exc:
                            report.errors.append(f"list {category.ranking_id}: {exc}")
                            log.exception("Ranking list %s failed", category.ranking_id)
                            continue
                        self.store.save_ranking(listing, federation=federation)
                        report.lists_fetched += 1
                        report.entries += len(listing.entries)
                        if report.lists_fetched % 10 == 0:
                            self._export(report)
        except KeyboardInterrupt:
            report.errors.append("interrupted")
            self._log("interrupted; writing clubs so far")
        self._export(report)
        report.clubs = self.store.stats()["clubs"]
        return report

    def _export(self, report: RankingSyncReport) -> None:
        path = self.store.export_clubs(self.clubs_path)
        report.clubs_path = str(path)
        stats = self.store.stats()
        self._log(
            f"  saved {stats['lists']} lists, {stats['entries']} athletes, {stats['clubs']} clubs -> {path}"
        )

    def _log(self, message: str) -> None:
        if self.progress is not None:
            print(message, file=self.progress, flush=True)
