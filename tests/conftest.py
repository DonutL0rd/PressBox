"""Shared fixtures for the press_box test suite."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tv_automator.providers.base import Game, GameStatus, Team

_PACIFIC = ZoneInfo("America/Los_Angeles")


def make_game(
    game_id: str = "12345",
    status: GameStatus = GameStatus.SCHEDULED,
    away: str = "NYY",
    home: str = "BOS",
    away_score: int | None = None,
    home_score: int | None = None,
    start_time: datetime | None = None,
) -> Game:
    if start_time is None:
        start_time = datetime(2024, 4, 1, 19, 10, tzinfo=_PACIFIC)
    return Game(
        game_id=game_id,
        provider="mlb",
        away_team=Team(name="New York Yankees", abbreviation=away, score=away_score),
        home_team=Team(name="Boston Red Sox", abbreviation=home, score=home_score),
        start_time=start_time,
        status=status,
        venue="Fenway Park",
    )
