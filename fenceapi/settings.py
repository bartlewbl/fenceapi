from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from fenceapi.client import DEFAULT_USER_AGENT, HttpClient


class SettingsError(ValueError):
    """Invalid or missing fenceapi.toml."""


@dataclass(frozen=True)
class ScrapeSettings:
    interval: float = 1.0
    jitter: float = 0.25
    lang: str = "en"
    user_agent: str | None = None


@dataclass(frozen=True)
class DailySettings:
    window: str = "06:00-22:00"
    interval: float = 2.0
    jitter: float = 1.0
    calendar: bool = True
    rankings: bool = True
    nation: str | None = None
    details: bool = False
    entries: bool = False
    limit: int | None = None
    federations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApiSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    rate_limit: int = 100
    rate_window: float = 60.0
    cors: tuple[str, ...] = ()
    api_key: str | None = None
    calendar_ttl: int = 1800
    event_ttl: int = 900
    snapshot_ttl: int = 600
    athlete_ttl: int = 3600


@dataclass(frozen=True)
class PathSettings:
    rankings_db: str = "data/rankings.sqlite"
    events_db: str = "data/events.sqlite"
    clubs: str = "data/clubs.json"
    lock: str = "data/daily-sync.lock"


@dataclass(frozen=True)
class Settings:
    scrape: ScrapeSettings = field(default_factory=ScrapeSettings)
    daily: DailySettings = field(default_factory=DailySettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    paths: PathSettings = field(default_factory=PathSettings)
    source: Path | None = None
    overlay: Path | None = None


_SECTIONS = {
    "scrape": ScrapeSettings,
    "daily": DailySettings,
    "api": ApiSettings,
    "paths": PathSettings,
}


def make_client(
    scrape: ScrapeSettings,
    *,
    interval: float | None = None,
    jitter: float | None = None,
) -> HttpClient:
    kwargs: dict[str, Any] = {
        "min_interval": scrape.interval if interval is None else interval,
        "jitter": scrape.jitter if jitter is None else jitter,
    }
    if scrape.user_agent:
        kwargs["user_agent"] = scrape.user_agent
    return HttpClient(**kwargs)


def load_settings(path: str | Path | None = None, *, cwd: Path | None = None) -> Settings:
    """Load defaults, then fenceapi.toml, then fenceapi.local.toml if present."""
    cwd = (cwd or Path.cwd()).resolve()
    if path is not None:
        primary = Path(path).expanduser()
        if not primary.is_file():
            raise SettingsError(f"settings file not found: {primary}")
        return _read(primary, overlay_local=True)
    env = os.environ.get("FENCEAPI_CONFIG")
    if env:
        primary = Path(env).expanduser()
        if not primary.is_file():
            raise SettingsError(f"FENCEAPI_CONFIG not found: {primary}")
        return _read(primary, overlay_local=True)
    primary = cwd / "fenceapi.toml"
    local = cwd / "fenceapi.local.toml"
    if primary.is_file():
        return _read(primary, overlay_local=True)
    if local.is_file():
        return _read(local, overlay_local=False)
    return Settings()


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    payload = {
        "source": str(settings.source) if settings.source else None,
        "overlay": str(settings.overlay) if settings.overlay else None,
        "scrape": asdict(settings.scrape),
        "daily": asdict(settings.daily),
        "api": asdict(settings.api),
        "paths": asdict(settings.paths),
    }
    payload["scrape"]["user_agent"] = settings.scrape.user_agent or DEFAULT_USER_AGENT
    payload["daily"]["federations"] = list(settings.daily.federations)
    payload["api"]["cors"] = list(settings.api.cors)
    payload["api"]["api_key"] = "(set)" if settings.api.api_key else None
    return payload


def _read(path: Path, *, overlay_local: bool) -> Settings:
    data = _load_toml(path)
    overlay_path: Path | None = None
    if overlay_local:
        candidate = path.with_name("fenceapi.local.toml")
        if candidate.is_file() and candidate.resolve() != path.resolve():
            overlay_path = candidate
            data = _deep_merge(data, _load_toml(candidate))
    settings = _from_dict(data)
    return Settings(
        scrape=settings.scrape,
        daily=settings.daily,
        api=settings.api,
        paths=settings.paths,
        source=path,
        overlay=overlay_path,
    )


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError(f"{path} must contain a table")
    return raw


def _from_dict(data: dict[str, Any]) -> Settings:
    unknown = set(data) - set(_SECTIONS)
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise SettingsError(f"unknown settings section: {keys}")
    scrape = _section("scrape", ScrapeSettings, data.get("scrape"))
    daily = _section("daily", DailySettings, data.get("daily"))
    api = _section("api", ApiSettings, data.get("api"))
    paths = _section("paths", PathSettings, data.get("paths"))
    _validate(scrape, daily, api)
    return Settings(scrape=scrape, daily=daily, api=api, paths=paths)


def _section(name: str, cls: type, raw: Any):
    if raw is None:
        return cls()
    if not isinstance(raw, dict):
        raise SettingsError(f"[{name}] must be a table")
    allowed = {item.name for item in fields(cls)}
    unknown = set(raw) - allowed
    if unknown:
        keys = ", ".join(sorted(unknown))
        raise SettingsError(f"unknown [{name}] key: {keys}")
    values: dict[str, Any] = {}
    for item in fields(cls):
        if item.name not in raw:
            continue
        values[item.name] = _coerce(name, item.name, raw[item.name])
    return cls(**values)


def _coerce(section: str, key: str, value: Any) -> Any:
    if key in {"interval", "jitter", "rate_window"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"[{section}] {key} must be a number")
        return float(value)
    if key in {"calendar", "rankings", "details", "entries"}:
        if not isinstance(value, bool):
            raise SettingsError(f"[{section}] {key} must be true or false")
        return value
    if key in {"port", "rate_limit", "calendar_ttl", "event_ttl", "snapshot_ttl", "athlete_ttl"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"[{section}] {key} must be an integer")
        return value
    if key == "limit":
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"[{section}] {key} must be an integer")
        return None if value <= 0 else value
    if key in {"federations", "cors"}:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SettingsError(f"[{section}] {key} must be an array of strings")
        return tuple(item.strip() for item in value if item.strip())
    if value is None:
        return None
    if not isinstance(value, str):
        raise SettingsError(f"[{section}] {key} must be a string")
    text = value.strip()
    if key in {"nation", "user_agent", "api_key"}:
        return text or None
    return text


def _validate(scrape: ScrapeSettings, daily: DailySettings, api: ApiSettings) -> None:
    from fenceapi.daily_sync import parse_window

    if scrape.interval <= 0 or daily.interval <= 0:
        raise SettingsError("interval must be greater than 0")
    if scrape.jitter < 0 or daily.jitter < 0:
        raise SettingsError("jitter cannot be negative")
    try:
        parse_window(daily.window)
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc
    if not daily.calendar and not daily.rankings:
        raise SettingsError("[daily] enable calendar and/or rankings")
    if not 1 <= api.port <= 65535:
        raise SettingsError("[api] port must be between 1 and 65535")
    if api.rate_limit < 0:
        raise SettingsError("[api] rate_limit cannot be negative")
    if api.rate_window <= 0:
        raise SettingsError("[api] rate_window must be greater than 0")
    for name in ("calendar_ttl", "event_ttl", "snapshot_ttl", "athlete_ttl"):
        if getattr(api, name) < 0:
            raise SettingsError(f"[api] {name} cannot be negative")


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out
