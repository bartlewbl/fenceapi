from __future__ import annotations

from pathlib import Path

import pytest

from fenceapi.settings import Settings, SettingsError, load_settings


def test_load_repo_toml() -> None:
    settings = load_settings(Path(__file__).resolve().parents[1] / "fenceapi.toml")
    assert settings.scrape.interval == 1.0
    assert settings.scrape.jitter == 0.25
    assert settings.daily.window == "06:00-22:00"
    assert settings.daily.interval == 2.0
    assert settings.daily.calendar is True
    assert settings.daily.rankings is True
    assert settings.daily.nation is None
    assert settings.daily.limit is None
    assert settings.daily.federations == ()
    assert settings.paths.lock == "data/daily-sync.lock"
    assert settings.api.host == "127.0.0.1"
    assert settings.api.port == 8000
    assert settings.api.rate_limit == 100
    assert settings.api.api_key is None
    assert settings.api.calendar_ttl == 1800


def test_local_overlay(tmp_path: Path) -> None:
    (tmp_path / "fenceapi.toml").write_text(
        '[daily]\nwindow = "06:00-22:00"\ninterval = 2.0\n',
        encoding="utf-8",
    )
    (tmp_path / "fenceapi.local.toml").write_text(
        '[daily]\nwindow = "08:00-18:00"\njitter = 2.5\ncalendar = false\n'
        'federations = ["ger", "fie"]\n'
        "[api]\nport = 9000\nrate_limit = 40\n",
        encoding="utf-8",
    )
    settings = load_settings(cwd=tmp_path)
    assert settings.daily.window == "08:00-18:00"
    assert settings.daily.interval == 2.0
    assert settings.daily.jitter == 2.5
    assert settings.daily.calendar is False
    assert settings.daily.federations == ("ger", "fie")
    assert settings.api.port == 9000
    assert settings.api.rate_limit == 40
    assert settings.overlay == (tmp_path / "fenceapi.local.toml").resolve()


def test_unknown_key_errors(tmp_path: Path) -> None:
    path = tmp_path / "fenceapi.toml"
    path.write_text("[scrape]\nhammer = 0.1\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="unknown \\[scrape\\] key: hammer"):
        load_settings(path)


def test_bad_window_errors(tmp_path: Path) -> None:
    path = tmp_path / "fenceapi.toml"
    path.write_text('[daily]\nwindow = "22:00-06:00"\n', encoding="utf-8")
    with pytest.raises(SettingsError, match="window"):
        load_settings(path)


def test_defaults_when_missing(tmp_path: Path) -> None:
    settings = load_settings(cwd=tmp_path)
    assert settings == Settings()
    assert settings.source is None
