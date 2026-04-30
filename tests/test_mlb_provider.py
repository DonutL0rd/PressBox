"""Tests for MLBProvider — schedule fetching, status mapping, sorting."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from tv_automator.providers.base import GameStatus
from tv_automator.providers.mlb import MLBProvider, _team_abbrev

_PACIFIC = ZoneInfo("America/Los_Angeles")

SAMPLE_GAME = {
    "game_id": 12345,
    "status": "In Progress",
    "game_datetime": "2024-04-01T22:10:00Z",
    "away_name": "New York Yankees",
    "home_name": "Boston Red Sox",
    "away_score": 3,
    "home_score": 2,
    "venue_name": "Fenway Park",
    "summary": "NYY @ BOS - 04/01/2024",
    "game_type": "R",
    "series_status": "",
    "national_broadcasts": "",
    "away_probable_pitcher": "Cole",
    "home_probable_pitcher": "Sale",
    "current_inning": 5,
    "inning_state": "Top",
}


# ── _team_abbrev ──────────────────────────────────────────────────

def test_team_abbrev_known_teams():
    assert _team_abbrev("New York Yankees") == "NYY"
    assert _team_abbrev("Los Angeles Dodgers") == "LAD"
    assert _team_abbrev("Boston Red Sox") == "BOS"
    assert _team_abbrev("Chicago Cubs") == "CHC"
    assert _team_abbrev("Kansas City Royals") == "KC"


def test_team_abbrev_unknown_falls_back_to_first_three_uppercase():
    assert _team_abbrev("Fictional Team Name") == "FIC"
    assert _team_abbrev("Xyz Squad") == "XYZ"


def test_team_abbrev_empty_string():
    assert _team_abbrev("") == ""


# ── Status mapping ────────────────────────────────────────────────

@pytest.mark.parametrize("status_str,expected", [
    ("In Progress", GameStatus.LIVE),
    ("Live", GameStatus.LIVE),
    ("Final", GameStatus.FINAL),
    ("Game Over", GameStatus.FINAL),
    ("Scheduled", GameStatus.SCHEDULED),
    ("Pre-Game", GameStatus.PRE_GAME),
    ("Warmup", GameStatus.PRE_GAME),
    ("Delayed", GameStatus.PRE_GAME),
    ("Delayed Start", GameStatus.PRE_GAME),
    ("Postponed", GameStatus.POSTPONED),
    ("Suspended", GameStatus.POSTPONED),
    ("Cancelled", GameStatus.CANCELLED),
])
@pytest.mark.asyncio
async def test_get_schedule_status_mapping(status_str, expected):
    game = dict(SAMPLE_GAME, status=status_str)
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[game]):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))
    assert len(games) == 1
    assert games[0].status == expected


@pytest.mark.asyncio
async def test_get_schedule_unknown_status_maps_to_unknown():
    game = dict(SAMPLE_GAME, status="Some Weird Status")
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[game]):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))
    assert games[0].status == GameStatus.UNKNOWN


# ── get_schedule ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_schedule_returns_game_fields():
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[SAMPLE_GAME]):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))

    assert len(games) == 1
    g = games[0]
    assert g.game_id == "12345"
    assert g.provider == "mlb"
    assert g.away_team.abbreviation == "NYY"
    assert g.home_team.abbreviation == "BOS"
    assert g.away_team.score == 3
    assert g.home_team.score == 2
    assert g.venue == "Fenway Park"
    assert g.extra["current_inning"] == 5
    assert g.extra["inning_state"] == "Top"
    assert g.extra["away_probable_pitcher"] == "Cole"


@pytest.mark.asyncio
async def test_get_schedule_converts_datetime_to_pacific():
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[SAMPLE_GAME]):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))

    g = games[0]
    # 2024-04-01T22:10:00Z = 3:10 PM Pacific
    assert g.start_time.tzinfo is not None
    assert g.start_time.hour == 15
    assert g.start_time.minute == 10


@pytest.mark.asyncio
async def test_get_schedule_sorts_live_before_scheduled_before_final():
    scheduled = dict(SAMPLE_GAME, game_id=1, status="Scheduled")
    live = dict(SAMPLE_GAME, game_id=2, status="In Progress")
    final = dict(SAMPLE_GAME, game_id=3, status="Final")

    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[final, scheduled, live]):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))

    assert games[0].status == GameStatus.LIVE
    assert games[1].status == GameStatus.SCHEDULED
    assert games[2].status == GameStatus.FINAL


@pytest.mark.asyncio
async def test_get_schedule_returns_empty_on_api_exception():
    with patch("tv_automator.providers.mlb.statsapi.schedule", side_effect=Exception("API down")):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))
    assert games == []


@pytest.mark.asyncio
async def test_get_schedule_returns_empty_for_no_games():
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[]):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))
    assert games == []


@pytest.mark.asyncio
async def test_get_schedule_skips_malformed_game_entries():
    bad_game = {"game_id": "bad", "game_datetime": "not-a-date"}
    good_game = dict(SAMPLE_GAME, game_id=99999)

    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[bad_game, good_game]):
        provider = MLBProvider()
        games = await provider.get_schedule(datetime(2024, 4, 1, tzinfo=_PACIFIC))

    # Bad entry is skipped, good entry is returned
    assert len(games) == 1
    assert games[0].game_id == "99999"


# ── get_game_status ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_game_status_live():
    game = dict(SAMPLE_GAME, status="In Progress")
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[game]):
        provider = MLBProvider()
        status = await provider.get_game_status("12345")
    assert status == GameStatus.LIVE


@pytest.mark.asyncio
async def test_get_game_status_final():
    game = dict(SAMPLE_GAME, status="Final")
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[game]):
        provider = MLBProvider()
        status = await provider.get_game_status("12345")
    assert status == GameStatus.FINAL


@pytest.mark.asyncio
async def test_get_game_status_returns_unknown_on_exception():
    with patch("tv_automator.providers.mlb.statsapi.schedule", side_effect=Exception("down")):
        provider = MLBProvider()
        status = await provider.get_game_status("12345")
    assert status == GameStatus.UNKNOWN


@pytest.mark.asyncio
async def test_get_game_status_returns_unknown_for_empty_response():
    with patch("tv_automator.providers.mlb.statsapi.schedule", return_value=[]):
        provider = MLBProvider()
        status = await provider.get_game_status("12345")
    assert status == GameStatus.UNKNOWN


# ── Provider metadata ─────────────────────────────────────────────

def test_provider_name():
    assert MLBProvider().name == "mlb"


def test_provider_display_name():
    assert MLBProvider().display_name == "MLB.TV"
