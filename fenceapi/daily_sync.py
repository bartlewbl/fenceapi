from __future__ import annotations

import json
import random
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Callable, TextIO

from fenceapi.scraper import Scraper
from fenceapi.store import utcnow


def parse_window(spec: str) -> tuple[dt_time, dt_time]:
    """Parse `HH:MM-HH:MM` into start and end times (end exclusive of overnight)."""
    raw = (spec or "").strip()
    if "-" not in raw:
        raise ValueError("window must look like 06:00-22:00")
    start_s, end_s = raw.split("-", 1)
    try:
        start = dt_time.fromisoformat(start_s.strip())
        end = dt_time.fromisoformat(end_s.strip())
    except ValueError as exc:
        raise ValueError("window must look like 06:00-22:00") from exc
    if end <= start:
        raise ValueError("window end must be after start on the same day")
    return start, end


def choose_run_at(
    now: datetime,
    window_start: dt_time,
    window_end: dt_time,
    rng: random.Random,
    *,
    min_remaining: float = 15 * 60,
) -> datetime:
    """Pick a random instant in today's remaining window, or tomorrow's full window."""
    if now.tzinfo is None:
        now = now.astimezone()
    today_start = datetime.combine(now.date(), window_start, tzinfo=now.tzinfo)
    today_end = datetime.combine(now.date(), window_end, tzinfo=now.tzinfo)
    if now < today_start:
        lo, hi = today_start, today_end
    elif now < today_end and (today_end - now).total_seconds() >= min_remaining:
        lo, hi = now, today_end
    else:
        lo = today_start + timedelta(days=1)
        hi = today_end + timedelta(days=1)
    span = (hi - lo).total_seconds()
    return lo + timedelta(seconds=rng.random() * span)


def _acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    except ImportError:
        return handle
    return handle


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def run_daily_sync(
    *,
    immediate: bool = False,
    window: str | None = None,
    interval: float | None = None,
    jitter: float | None = None,
    do_calendar: bool | None = None,
    do_rankings: bool | None = None,
    nation: str | None = None,
    calendar_details: bool | None = None,
    calendar_entries: bool | None = None,
    calendar_limit: int | None = None,
    federations: list[str] | tuple[str, ...] | None = None,
    db: str | None = None,
    events_db: str | None = None,
    clubs_out: str | None = None,
    lock_path: str | None = None,
    lang: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    rng: random.Random | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: TextIO = sys.stderr,
    settings: Any | None = None,
) -> dict[str, Any]:
    from fenceapi.settings import Settings, load_settings, make_client

    cfg: Settings = settings or load_settings()
    window = cfg.daily.window if window is None else window
    interval = cfg.daily.interval if interval is None else interval
    jitter = cfg.daily.jitter if jitter is None else jitter
    do_calendar = cfg.daily.calendar if do_calendar is None else do_calendar
    do_rankings = cfg.daily.rankings if do_rankings is None else do_rankings
    nation = cfg.daily.nation if nation is None else nation
    calendar_details = cfg.daily.details if calendar_details is None else calendar_details
    calendar_entries = cfg.daily.entries if calendar_entries is None else calendar_entries
    calendar_limit = cfg.daily.limit if calendar_limit is None else calendar_limit
    if federations is None:
        federations = cfg.daily.federations
    db = cfg.paths.rankings_db if db is None else db
    events_db = cfg.paths.events_db if events_db is None else events_db
    clubs_out = cfg.paths.clubs if clubs_out is None else clubs_out
    lock_path = cfg.paths.lock if lock_path is None else lock_path
    lang = cfg.scrape.lang if lang is None else lang
    rng = rng or random.Random()
    current = now or datetime.now().astimezone()
    report: dict[str, Any] = {
        "ok": True,
        "started_at": utcnow(),
        "config": str(cfg.source) if cfg.source else None,
        "window": window,
        "jobs": [name for name, on in (("calendar", do_calendar), ("rankings", do_rankings)) if on],
        "interval": interval,
        "jitter": jitter,
    }
    if federations:
        report["federations"] = list(federations)
    if not report["jobs"]:
        raise ValueError("Nothing to do: enable calendar and/or rankings")

    if immediate:
        run_at = current
        delay = 0.0
    else:
        start, end = parse_window(window)
        run_at = choose_run_at(current, start, end, rng)
        delay = max(0.0, (run_at - current).total_seconds())
    report["run_at"] = run_at.isoformat()
    report["sleep_seconds"] = round(delay)

    if dry_run:
        print(
            f"daily-sync: would sleep {_format_duration(delay)} until {run_at.isoformat()}",
            file=log,
            flush=True,
        )
        return report

    if delay > 0:
        print(
            f"daily-sync: sleeping {_format_duration(delay)} until {run_at.isoformat()} "
            f"(window {window})",
            file=log,
            flush=True,
        )
        sleep(delay)

    lock = _acquire_lock(Path(lock_path))
    if lock is None:
        report["ok"] = False
        report["error"] = "another daily-sync is already running"
        print(f"daily-sync: {report['error']}", file=log, flush=True)
        return report
    try:
        scraper = Scraper(
            client=make_client(cfg.scrape, interval=interval, jitter=jitter),
            lang=lang,
        )
        if do_calendar:
            from fenceapi.event_store import EventStore
            from fenceapi.service import ApiService
            from fenceapi.store import RankingStore

            print("daily-sync: refreshing calendar cache", file=log, flush=True)
            service = ApiService(scraper, EventStore(events_db), RankingStore(db))
            report["calendar"] = service.sync_calendar(
                nation=nation,
                details=calendar_details,
                entries=calendar_entries,
                limit=calendar_limit,
            )
        if do_rankings:
            from fenceapi.ranking_sync import RankingSyncer
            from fenceapi.store import RankingStore

            print("daily-sync: refreshing current rankings", file=log, flush=True)
            syncer = RankingSyncer(scraper, RankingStore(db), clubs_path=clubs_out, progress=log)
            ranking_report = syncer.run(
                federations=list(federations) or None,
                refresh_current=True,
            )
            report["rankings"] = ranking_report.to_dict()
            if ranking_report.errors:
                report["ok"] = False
        report["finished_at"] = utcnow()
        return report
    finally:
        lock.close()


def dump_report(report: dict[str, Any], out: TextIO = sys.stdout) -> None:
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
