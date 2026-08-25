from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from random import Random

import pytest

from fenceapi.daily_sync import choose_run_at, parse_window, run_daily_sync
from fenceapi.settings import Settings


def test_parse_window() -> None:
    start, end = parse_window("06:00-22:00")
    assert start == time(6, 0)
    assert end == time(22, 0)
    with pytest.raises(ValueError):
        parse_window("22:00-06:00")
    with pytest.raises(ValueError):
        parse_window("nope")


def test_choose_run_at_before_window() -> None:
    now = datetime(2026, 8, 25, 0, 15, tzinfo=timezone.utc)
    chosen = [
        choose_run_at(now, time(6, 0), time(22, 0), Random(seed))
        for seed in range(40)
    ]
    hours = {item.hour for item in chosen}
    assert all(item.date() == now.date() for item in chosen)
    assert all(time(6, 0) <= item.time() < time(22, 0) for item in chosen)
    assert len(hours) >= 8


def test_choose_run_at_inside_window_uses_remaining_hours() -> None:
    now = datetime(2026, 8, 25, 18, 0, tzinfo=timezone.utc)
    chosen = choose_run_at(now, time(6, 0), time(22, 0), Random(1))
    assert chosen.date() == now.date()
    assert now <= chosen < datetime(2026, 8, 25, 22, 0, tzinfo=timezone.utc)


def test_choose_run_at_after_window_uses_tomorrow() -> None:
    now = datetime(2026, 8, 25, 22, 30, tzinfo=timezone.utc)
    chosen = choose_run_at(now, time(6, 0), time(22, 0), Random(1))
    assert chosen.date() == now.date() + timedelta(days=1)
    assert time(6, 0) <= chosen.time() < time(22, 0)


def test_dry_run_does_not_sleep() -> None:
    slept: list[float] = []
    report = run_daily_sync(
        dry_run=True,
        now=datetime(2026, 8, 25, 0, 15, tzinfo=timezone.utc),
        rng=Random(0),
        sleep=slept.append,
        settings=Settings(),
    )
    assert slept == []
    assert report["sleep_seconds"] > 0
    assert report["jobs"] == ["calendar", "rankings"]


def test_now_skips_wait() -> None:
    slept: list[float] = []
    report = run_daily_sync(
        immediate=True,
        dry_run=True,
        now=datetime(2026, 8, 25, 0, 15, tzinfo=timezone.utc),
        sleep=slept.append,
        settings=Settings(),
    )
    assert report["sleep_seconds"] == 0
    assert slept == []
