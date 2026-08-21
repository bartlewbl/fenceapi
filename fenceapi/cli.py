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
from fenceapi.client import HttpClient
from fenceapi.scraper import Scraper


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fenceapi",
        description="Scrape tournament data from fencingworldwide.com",
    )
    parser.add_argument("--lang", default="en")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Minimum seconds between HTTP requests (default: 1.0)",
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
    rankings.add_argument("--db", default="data/rankings.sqlite", help="SQLite path for bulk ranking/club storage")
    rankings.add_argument("--clubs-out", default="data/clubs.json", help="JSON club list written during bulk sync")

    clubs = sub.add_parser("clubs", help="Show or export the scraped club list")
    clubs.add_argument("--db", default="data/rankings.sqlite")
    clubs.add_argument("-o", "--output", default=None, help="Write clubs JSON here (default: stdout)")

    serve = sub.add_parser("serve", help="Run the HTTP API on this machine")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--db", default="data/rankings.sqlite")
    serve.add_argument("--events-db", default="data/events.sqlite")
    serve.add_argument(
        "--rate-limit",
        type=int,
        default=None,
        help="Max requests per IP per minute (default: 100). Use 0 to disable.",
    )

    sync_cal = sub.add_parser("sync-calendar", help="Refresh the local calendar/event cache")
    sync_cal.add_argument("nation", nargs="?", help="NOC filter, e.g. GER")
    sync_cal.add_argument("--details", action="store_true", help="Also fetch each event page")
    sync_cal.add_argument("--entries", action="store_true", help="Also fetch entry lists")
    sync_cal.add_argument("--limit", type=int, help="Max events to expand")
    sync_cal.add_argument("--db", default="data/rankings.sqlite")
    sync_cal.add_argument("--events-db", default="data/events.sqlite")

    args = parser.parse_args(argv)
    if args.command == "clubs":
        return _clubs(args)
    if args.command == "serve":
        return _serve(args)

    scraper = Scraper(
        client=HttpClient(min_interval=args.interval if args.interval is not None else 1.0),
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


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from fenceapi.api import create_app
    from fenceapi.client import HttpClient
    from fenceapi.event_store import EventStore
    from fenceapi.service import ApiService
    from fenceapi.store import RankingStore

    interval = args.interval if args.interval is not None else 1.0
    service = ApiService(
        Scraper(client=HttpClient(min_interval=interval), lang=args.lang),
        EventStore(args.events_db),
        RankingStore(args.db),
    )
    uvicorn.run(
        create_app(service, rate_limit=args.rate_limit),
        host=args.host,
        port=args.port,
    )
    return 0


def _rankings(scraper: Scraper, args: argparse.Namespace) -> Any:
    if args.all_regions or args.all_seasons:
        from fenceapi.ranking_sync import RankingSyncer
        from fenceapi.store import RankingStore

        store = RankingStore(args.db)
        syncer = RankingSyncer(scraper, store, clubs_path=args.clubs_out)
        federations = None if args.all_regions else [args.federation] if args.federation else None
        if federations is None and not args.all_regions:
            raise SystemExit("Pass a federation or --all-regions")
        report = syncer.run(
            federations=federations,
            all_seasons=bool(args.all_seasons),
            season=None if args.all_seasons else args.season,
            weapon=args.weapon,
            gender=args.gender,
            age=args.age,
            kind=args.kind,
            group=args.group,
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


if __name__ == "__main__":
    raise SystemExit(main())
