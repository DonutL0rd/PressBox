"""Tests for GameScheduler — provider management, schedule access, auto-start."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from tv_automator.providers.base import Game, GameStatus, StreamingProvider, Team
from tv_automator.scheduler.game_scheduler import GameScheduler
from tv_automator.settings import AppSettings

_PACIFIC = ZoneInfo("America/Los_Angeles")


# ── Helpers ───────────────────────────────────────────────────────

def make_settings(tmp_path, auto_start=False, favorites=None, poll_interval=60):
    s = AppSettings(tmp_path)
    s.update({
        "auto_start": auto_start,
        "favorite_teams": favorites or [],
        "poll_interval": poll_interval,
    })
    return s


def make_game(game_id, status=GameStatus.SCHEDULED, away="NYY", home="BOS"):
    return Game(
        game_id=game_id,
        provider="mlb",
        away_team=Team(name="Team A", abbreviation=away),
        home_team=Team(name="Team B", abbreviation=home),
        start_time=datetime(2024, 4, 1, 19, 0, tzinfo=_PACIFIC),
        status=status,
    )


class MockProvider(StreamingProvider):
    def __init__(self, name="mlb", games=None):
        self._name = name
        self._games = games or []

    @property
    def name(self):
        return self._name

    @property
    def display_name(self):
        return self._name.upper()

    async def get_schedule(self, date):
        return list(self._games)

    async def get_game_status(self, game_id):
        for g in self._games:
            if g.game_id == game_id:
                return g.status
        return GameStatus.UNKNOWN


# ── Provider registration ─────────────────────────────────────────

def test_register_provider(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [])
    sched.register_provider(p)
    assert "mlb" in sched.providers


def test_get_provider_returns_registered(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [])
    sched.register_provider(p)
    assert sched.get_provider("mlb") is p


def test_get_provider_returns_none_for_unknown(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    assert sched.get_provider("nonexistent") is None


# ── Schedule access ───────────────────────────────────────────────

def test_get_games_for_provider_empty_initially(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [])
    sched.register_provider(p)
    assert sched.get_games_for_provider("mlb") == []


def test_get_games_for_provider_after_setting_schedule(tmp_path):
    game = make_game("1")
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [game]
    games = sched.get_games_for_provider("mlb")
    assert len(games) == 1
    assert games[0].game_id == "1"


def test_get_games_for_provider_unknown_returns_empty(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    assert sched.get_games_for_provider("nba") == []


def test_get_game_by_id_found(tmp_path):
    game = make_game("999")
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [game]
    assert sched.get_game_by_id("999") is game


def test_get_game_by_id_not_found(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [])
    sched.register_provider(p)
    assert sched.get_game_by_id("999") is None


def test_get_live_games_filters_correctly(tmp_path):
    live = make_game("1", GameStatus.LIVE)
    scheduled = make_game("2", GameStatus.SCHEDULED)
    final = make_game("3", GameStatus.FINAL)
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live, scheduled, final]

    live_games = sched.get_live_games()
    assert len(live_games) == 1
    assert live_games[0].game_id == "1"


def test_get_all_games_sorted_live_first(tmp_path):
    final = make_game("1", GameStatus.FINAL)
    live = make_game("2", GameStatus.LIVE)
    pre = make_game("3", GameStatus.PRE_GAME)
    scheduled = make_game("4", GameStatus.SCHEDULED)

    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [])
    sched.register_provider(p)
    sched._schedules["mlb"] = [final, scheduled, pre, live]

    all_games = sched.get_all_games()
    statuses = [g.status for g in all_games]
    assert statuses[0] == GameStatus.LIVE
    assert statuses[1] == GameStatus.PRE_GAME
    assert statuses[2] == GameStatus.SCHEDULED
    assert statuses[3] == GameStatus.FINAL


# ── Refresh ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_populates_schedule(tmp_path):
    game = make_game("1")
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [game])
    sched.register_provider(p)

    await sched.refresh()

    assert len(sched.get_games_for_provider("mlb")) == 1


@pytest.mark.asyncio
async def test_refresh_calls_on_refresh_callback(tmp_path):
    on_refresh = AsyncMock()
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [make_game("1")])
    sched.register_provider(p)
    sched.set_on_refresh(on_refresh)

    await sched.refresh()

    on_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_handles_provider_exception_gracefully(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    bad_provider = MockProvider("mlb", [])
    bad_provider.get_schedule = AsyncMock(side_effect=Exception("API down"))
    sched.register_provider(bad_provider)

    # Should not raise
    await sched.refresh()
    assert sched.get_games_for_provider("mlb") == []


# ── Auto-start ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_start_fires_for_favorite_team(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="NYY", home="BOS")

    settings = make_settings(tmp_path, auto_start=True, favorites=["NYY"])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)

    await sched._check_auto_start()

    callback.assert_called_once_with(p, live_game)


@pytest.mark.asyncio
async def test_auto_start_fires_for_home_team_favorite(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="NYY", home="LAD")

    settings = make_settings(tmp_path, auto_start=True, favorites=["LAD"])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)

    await sched._check_auto_start()

    callback.assert_called_once()


@pytest.mark.asyncio
async def test_auto_start_skips_already_started_game(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="NYY")

    settings = make_settings(tmp_path, auto_start=True, favorites=["NYY"])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)
    sched._auto_started_games.add("1")  # already triggered

    await sched._check_auto_start()

    callback.assert_not_called()


@pytest.mark.asyncio
async def test_auto_start_not_triggered_when_disabled(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="NYY")

    settings = make_settings(tmp_path, auto_start=False, favorites=["NYY"])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)

    await sched._check_auto_start()

    callback.assert_not_called()


@pytest.mark.asyncio
async def test_auto_start_not_triggered_with_no_favorites(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="NYY")

    settings = make_settings(tmp_path, auto_start=True, favorites=[])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)

    await sched._check_auto_start()

    callback.assert_not_called()


@pytest.mark.asyncio
async def test_auto_start_not_triggered_for_non_favorite(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="NYY", home="BOS")

    settings = make_settings(tmp_path, auto_start=True, favorites=["LAD"])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)

    await sched._check_auto_start()

    callback.assert_not_called()


@pytest.mark.asyncio
async def test_auto_start_only_fires_once_per_game(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="NYY")

    settings = make_settings(tmp_path, auto_start=True, favorites=["NYY"])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)

    await sched._check_auto_start()
    await sched._check_auto_start()  # second call — game already in auto_started_games

    callback.assert_called_once()


@pytest.mark.asyncio
async def test_auto_start_case_insensitive_matching(tmp_path):
    callback = AsyncMock()
    live_game = make_game("1", GameStatus.LIVE, away="nyy")  # lowercase abbrev

    settings = make_settings(tmp_path, auto_start=True, favorites=["NYY"])
    sched = GameScheduler(settings)
    p = MockProvider("mlb", [live_game])
    sched.register_provider(p)
    sched._schedules["mlb"] = [live_game]
    sched.set_auto_start_callback(callback)

    await sched._check_auto_start()

    callback.assert_called_once()


# ── Stop ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stop_cancels_poll_task(tmp_path):
    sched = GameScheduler(make_settings(tmp_path))
    p = MockProvider("mlb", [])
    sched.register_provider(p)

    await sched.start()
    assert sched._poll_task is not None

    await sched.stop()
    assert sched._poll_task is None
