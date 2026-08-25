from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from fenceapi.calendar_query import (
    calendar_ageclass as _calendar_ageclass,
    calendar_discipline as _calendar_discipline,
    calendar_gender as _calendar_gender,
    calendar_group as _calendar_group,
    calendar_venue as _calendar_venue,
)
from fenceapi.scraper import Scraper
from fenceapi.settings import SettingsError, load_settings, make_client, settings_to_dict


def main(argv: list[str] | None = None) -> int:
    try:
        settings = load_settings(_preparse_config(argv))
    except SettingsError as exc:
        raise SystemExit(str(exc)) from exc

    parser = argparse.ArgumentParser(
        prog="fenceapi",
        description="Scrape tournament data from fencingworldwide.com",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Settings file (default: ./fenceapi.toml, overlay fenceapi.local.toml)",
    )
    parser.add_argument("--lang", default=settings.scrape.lang)
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.scrape.interval,
        help="Minimum seconds between HTTP requests (default: from fenceapi.toml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    home = sub.add_parser("current", help="Current, upcoming, and recent tournaments")
    home.add_argument("--nation", help="NOC filter, e.g. ger, bra, sui")

    archive = sub.add_parser("archive", help="Year archive")
    archive.add_argument("year", type=int)

    tournament = sub.add_parser("tournament", help="Tournament overview and competitions")
    tournament.add_argument("key", help="id-year, e.g. 33940-2026")

    results = sub.add_parser("results", help="Competition results")
    results.add_argument("key", help="competition id-year, e.g. 916515-2025")

    participants = sub.add_parser("participants", help="Competition start list")
    participants.add_argument("key")

    timetable = sub.add_parser("timetable", help="Competition schedule")
    timetable.add_argument("key")

    calendar = sub.add_parser(
        "calendar",
        help="Ophardt tournament calendar (https://fencing.ophardt.online/en/calendar)",
    )
    calendar.add_argument(
        "nation",
        nargs="?",
        help="NOC filter, e.g. GER. Omit for all nations.",
    )
    calendar.add_argument("--from", dest="date_from", help="Start date YYYY-MM-DD")
    calendar.add_argument("--to", dest="date_to", help="End date YYYY-MM-DD")
    calendar.add_argument("--region", help="Region code, e.g. SN")
    calendar.add_argument("--city")
    calendar.add_argument("--title", help="Title substring")
    calendar.add_argument(
        "--type",
        dest="event_type",
        help="international, zonal, national, regional",
    )
    calendar.add_argument("--weapon", help="epee, foil, sabre")
    calendar.add_argument("--gender", help="men, women, open, mixed")
    calendar.add_argument("--age", help="senior, u20, u17, ...")
    calendar.add_argument(
        "--venue",
        default="tournament",
        help="tournament (default), examination, courses, training, camp, assembly, celebration",
    )
    calendar.add_argument(
        "--details",
        action="store_true",
        help="Also fetch the tournament info page for each event",
    )
    calendar.add_argument(
        "--entries",
        action="store_true",
        help="Also fetch entry lists (implies --details; many requests)",
    )
    calendar.add_argument("--limit", type=int, help="Max events to return / expand")
    calendar.add_argument(
        "--json-widget",
        action="store_true",
        help="Use the nation calendar-json widget instead of the HTML calendar",
    )

    event = sub.add_parser("event", help="Ophardt tournament info (widget/event)")
    event.add_argument("id", help="Event id, e.g. 34860")
    event.add_argument(
        "--entries",
        action="store_true",
        help="Also fetch the public entries list",
    )

    snapshot = sub.add_parser("snapshot", help="Home page plus tournament details")
    snapshot.add_argument("--nation")
    snapshot.add_argument(
        "--with-results",
        action="store_true",
        help="Also fetch result lists for each competition (many requests)",
    )

    rankings = sub.add_parser(
        "rankings",
        help="Ophardt rankings: federation, then weapon gender age",
    )
    rankings.add_argument(
        "federation",
        nargs="?",
        help="ger, fie, efc, a NOC code, or numeric id. Omit to list federations.",
    )
    rankings.add_argument("weapon", nargs="?", help="epee, foil, sabre")
    rankings.add_argument("gender", nargs="?", help="men, women")
    rankings.add_argument("age", nargs="?", help="senior, u20, u17, u15, ...")
    rankings.add_argument("--season", type=int, help="Season start year, e.g. 2025")
    rankings.add_argument("--kind", help="individual (default match) or team")
    rankings.add_argument("--group", help="Substring of the ranking group title")
    rankings.add_argument(
        "--all",
        action="store_true",
        help="Download every matching published list, not just the catalog",
    )
    rankings.add_argument("--limit", type=int, help="Max athletes to keep per list")
    rankings.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Seconds between HTTP requests (overrides global --interval)",
    )
    rankings.add_argument(
        "--all-regions",
        action="store_true",
        help="Every federation on the rankings index (international, national, regional)",
    )
    rankings.add_argument(
        "--all-seasons",
        action="store_true",
        help="Every season in that federation's dropdown",
    )
    rankings.add_argument(
        "--refresh-current",
        action="store_true",
        help="Re-fetch current-season lists (or --season); append a snapshot only when the table changed",
    )
    rankings.add_argument("--db", default=settings.paths.rankings_db, help="SQLite path for bulk ranking/club storage")
    rankings.add_argument("--clubs-out", default=settings.paths.clubs, help="JSON club list written during bulk sync")

    clubs = sub.add_parser("clubs", help="Show or export the scraped club list")
    clubs.add_argument("--db", default=settings.paths.rankings_db)
    clubs.add_argument("-o", "--output", default=None, help="Write clubs JSON here (default: stdout)")

    athlete = sub.add_parser("athlete", help="Ophardt athlete biography and ranking history")
    athlete.add_argument("athlete_id", type=int, help="Ophardt athlete id, e.g. 39083")
    athlete.add_argument("--db", default=settings.paths.rankings_db)
    athlete.add_argument(
        "--include",
        help=(
            "Comma-separated sections, e.g. medals or overview. "
            "Also: exams, results, match_stats, season_rankings, selections, "
            "memberships, rankings; aliases overview, profile, history"
        ),
    )
    athlete.add_argument(
        "--history-only",
        action="store_true",
        help="Only print ranking snapshots from the local SQLite database",
    )

    serve = sub.add_parser("serve", help="Run the HTTP API on this machine")
    serve.add_argument("--host", default=settings.api.host)
    serve.add_argument("--port", type=int, default=settings.api.port)
    serve.add_argument("--db", default=settings.paths.rankings_db)
    serve.add_argument("--events-db", default=settings.paths.events_db)
    serve.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        help="Max requests per IP per minute (default: from fenceapi.toml [api]). Use 0 to disable.",
    )

    sync_cal = sub.add_parser("sync-calendar", help="Refresh the local calendar/event cache")
    sync_cal.add_argument("nation", nargs="?", help="NOC filter, e.g. GER")
    sync_cal.add_argument("--details", action="store_true", help="Also fetch each event page")
    sync_cal.add_argument("--entries", action="store_true", help="Also fetch entry lists")
    sync_cal.add_argument("--limit", type=int, help="Max events to expand")
    sync_cal.add_argument("--db", default=settings.paths.rankings_db)
    sync_cal.add_argument("--events-db", default=settings.paths.events_db)

    daily = sub.add_parser(
        "daily-sync",
        help="Once-a-day scrape at a random hour, then refresh calendar and current rankings",
    )
    daily.add_argument(
        "--now",
        action="store_true",
        help="Skip the random wait and scrape immediately",
    )
    daily.add_argument(
        "--window",
        default=None,
        help="Local HH:MM-HH:MM window (default: from fenceapi.toml)",
    )
    daily.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the chosen start time and exit without scraping",
    )
    daily.add_argument(
        "--skip-calendar",
        action="store_true",
        help="Do not refresh the calendar cache",
    )
    daily.add_argument(
        "--skip-rankings",
        action="store_true",
        help="Do not refresh current-season ranking lists",
    )
    daily.add_argument("nation", nargs="?", help="Optional NOC filter for calendar, e.g. GER")
    daily.add_argument("--details", action="store_true", help="Also fetch each calendar event page")
    daily.add_argument("--entries", action="store_true", help="Also fetch entry lists")
    daily.add_argument("--limit", type=int, help="Max events to expand")
    daily.add_argument("--db", default=settings.paths.rankings_db)
    daily.add_argument("--events-db", default=settings.paths.events_db)
    daily.add_argument("--clubs-out", default=settings.paths.clubs)
    daily.add_argument("--lock", default=settings.paths.lock, help="Path of the scrape lock file")
    daily.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Minimum seconds between HTTP requests (default: from fenceapi.toml [daily])",
    )
    daily.add_argument(
        "--jitter",
        type=float,
        default=None,
        help="Extra random seconds after each request gap (default: from fenceapi.toml [daily])",
    )

    sub.add_parser("settings", help="Print the resolved scraping settings")

    args = parser.parse_args(argv)
    if args.command == "settings":
        json.dump(settings_to_dict(settings), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "clubs":
        return _clubs(args)
    if args.command == "athlete":
        return _athlete(args, settings)
    if args.command == "serve":
        return _serve(args, settings)
    if args.command == "daily-sync":
        return _daily_sync(args, settings)

    scraper = Scraper(
        client=make_client(settings.scrape, interval=args.interval),
        lang=args.lang,
    )
    payload = _run(scraper, args)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def _run(scraper: Scraper, args: argparse.Namespace) -> Any:
    command = args.command
    if command == "current":
        return scraper.home(nation=args.nation).to_dict()
    if command == "archive":
        return [item.to_dict() for item in scraper.archive(args.year)]
    if command == "tournament":
        return scraper.tournament(args.key).to_dict()
    if command == "results":
        return scraper.results(args.key).to_dict()
    if command == "participants":
        return scraper.participants(args.key).to_dict()
    if command == "timetable":
        return scraper.timetable(args.key).to_dict()
    if command == "calendar":
        return _calendar(scraper, args)
    if command == "event":
        return scraper.event(args.id, include_entries=args.entries).to_dict()
    if command == "snapshot":
        return scraper.snapshot(
            nation=args.nation,
            include_competitions=True,
            include_results=args.with_results,
        )
    if command == "rankings":
        return _rankings(scraper, args)
    if command == "sync-calendar":
        return _sync_calendar(scraper, args)
    raise ValueError(command)


def _calendar(scraper: Scraper, args: argparse.Namespace) -> Any:
    if args.json_widget:
        if not args.nation:
            raise SystemExit("calendar --json-widget needs a nation, e.g. GER")
        return scraper.ophardt_calendar(args.nation)
    events = scraper.calendar(
        nation=args.nation,
        date_from=args.date_from,
        date_to=args.date_to,
        region=args.region,
        city=args.city,
        title=args.title,
        group=_calendar_group(args.event_type),
        discipline=_calendar_discipline(args.weapon),
        gender=_calendar_gender(args.gender),
        ageclass=_calendar_ageclass(args.age),
        venuetype=_calendar_venue(args.venue),
    )
    if args.limit is not None:
        events = events[: args.limit]
    payload: dict[str, Any] = {
        "count": len(events),
        "events": [item.to_dict() for item in events],
    }
    if args.details or args.entries:
        payload["details"] = [
            scraper.event(item.event_id, include_entries=args.entries).to_dict()
            for item in events
        ]
    return payload


def _preparse_config(argv: list[str] | None) -> str | None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config")
    known, _ = pre.parse_known_args(argv)
    return known.config


def _daily_sync(args: argparse.Namespace, settings) -> int:
    from fenceapi.daily_sync import dump_report, run_daily_sync

    report = run_daily_sync(
        immediate=bool(args.now),
        window=args.window,
        interval=args.interval,
        jitter=args.jitter,
        do_calendar=False if args.skip_calendar else None,
        do_rankings=False if args.skip_rankings else None,
        nation=args.nation,
        calendar_details=True if args.details else None,
        calendar_entries=True if args.entries else None,
        calendar_limit=args.limit,
        db=args.db,
        events_db=args.events_db,
        clubs_out=args.clubs_out,
        lock_path=args.lock,
        lang=args.lang,
        dry_run=bool(args.dry_run),
        settings=settings,
    )
    dump_report(report)
    return 0 if report.get("ok") else 1


def _sync_calendar(scraper: Scraper, args: argparse.Namespace) -> Any:
    from fenceapi.event_store import EventStore
    from fenceapi.service import ApiService
    from fenceapi.store import RankingStore

    service = ApiService(scraper, EventStore(args.events_db), RankingStore(args.db))
    return service.sync_calendar(
        nation=args.nation,
        details=args.details,
        entries=args.entries,
        limit=args.limit,
    )


def _serve(args: argparse.Namespace, settings) -> int:
    import uvicorn

    from fenceapi.api import create_app, service_from_settings

    service = service_from_settings(
        settings,
        interval=args.interval,
        lang=args.lang,
        rankings_db=args.db,
        events_db=args.events_db,
    )
    uvicorn.run(
        create_app(service, settings=settings, rate_limit=args.rate_limit),
        host=args.host,
        port=args.port,
    )
    return 0


def _rankings(scraper: Scraper, args: argparse.Namespace) -> Any:
    if args.all_regions or args.all_seasons or args.refresh_current:
        from fenceapi.ranking_sync import RankingSyncer
        from fenceapi.store import RankingStore

        store = RankingStore(args.db)
        syncer = RankingSyncer(scraper, store, clubs_path=args.clubs_out)
        federations = None if args.all_regions else [args.federation] if args.federation else None
        if federations is None and not args.all_regions:
            raise SystemExit("Pass a federation or --all-regions")
        report = syncer.run(
            federations=federations,
            all_seasons=bool(args.all_seasons) and not bool(args.refresh_current),
            season=args.season,
            weapon=args.weapon,
            gender=args.gender,
            age=args.age,
            kind=args.kind,
            group=args.group,
            refresh_current=bool(args.refresh_current),
        )
        return report.to_dict()
    if not args.federation:
        return [item.to_dict() for item in scraper.ranking_federations()]
    specified = [args.weapon, args.gender, args.age]
    incomplete = any(specified) and not all(specified)
    fetch_lists = bool(args.all) or (all(specified) and not incomplete)
    if incomplete and not args.all:
        catalog = scraper.rankings(
            args.federation,
            weapon=args.weapon,
            gender=args.gender,
            age=args.age,
            kind=args.kind,
            group=args.group,
            season=args.season,
            fetch_lists=False,
        )
        catalog["hint"] = (
            "Pass weapon gender age to download a list, or --all to download every match."
        )
        return catalog
    return scraper.rankings(
        args.federation,
        weapon=args.weapon,
        gender=args.gender,
        age=args.age,
        kind=args.kind,
        group=args.group,
        season=args.season,
        fetch_lists=fetch_lists,
        limit=args.limit,
    )


def _clubs(args: argparse.Namespace) -> int:
    from fenceapi.store import RankingStore

    store = RankingStore(args.db)
    if args.output:
        path = store.export_clubs(args.output)
        json.dump({"ok": True, "path": str(path), **store.stats()}, sys.stdout, ensure_ascii=False, indent=2)
    else:
        clubs = store.list_clubs()
        json.dump({"count": len(clubs), "clubs": clubs}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


def _athlete(args: argparse.Namespace, settings) -> int:
    from fenceapi.service import parse_athlete_include, slice_athlete_payload
    from fenceapi.store import RankingStore

    history = RankingStore(args.db).athlete_history(args.athlete_id)
    if args.history_only:
        if history is None:
            json.dump(
                {"error": f"No ranking history for athlete {args.athlete_id}."},
                sys.stdout,
                ensure_ascii=False,
                indent=2,
            )
            sys.stdout.write("\n")
            return 1
        json.dump(history, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    scraper = Scraper(
        client=make_client(settings.scrape, interval=args.interval),
        lang=args.lang,
    )
    payload = scraper.athlete(args.athlete_id).to_dict()
    if history:
        payload["rankings"] = history["rankings"]
        payload["club_history"] = history["clubs"]
        if payload.get("yob") is None:
            payload["yob"] = history.get("yob")
    try:
        payload = slice_athlete_payload(payload, parse_athlete_include(args.include))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
