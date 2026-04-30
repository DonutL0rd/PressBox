"""Tests for base provider models — Game, GameStatus, Team."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tv_automator.providers.base import Game, GameStatus, Team

_PACIFIC = ZoneInfo("America/Los_Angeles")


def make_game(
    status: GameStatus = GameStatus.SCHEDULED,
    away_score: int | None = None,
    home_score: int | None = None,
) -> Game:
    return Game(
        game_id="123",
        provider="mlb",
        away_team=Team(name="New York Yankees", abbreviation="NYY", score=away_score),
        home_team=Team(name="Boston Red Sox", abbreviation="BOS", score=home_score),
        start_time=datetime(2024, 4, 1, 19, 10, tzinfo=_PACIFIC),
        status=status,
        venue="Fenway Park",
    )


# ── GameStatus ────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [
    (GameStatus.LIVE, True),
    (GameStatus.PRE_GAME, True),
    (GameStatus.SCHEDULED, False),
    (GameStatus.FINAL, False),
    (GameStatus.POSTPONED, False),
    (GameStatus.CANCELLED, False),
    (GameStatus.UNKNOWN, False),
])
def test_game_status_is_watchable(status, expected):
    assert status.is_watchable == expected


@pytest.mark.parametrize("status,label", [
    (GameStatus.SCHEDULED, "Scheduled"),
    (GameStatus.PRE_GAME, "Pre-Game"),
    (GameStatus.LIVE, "LIVE"),
    (GameStatus.FINAL, "Final"),
    (GameStatus.POSTPONED, "Postponed"),
    (GameStatus.CANCELLED, "Cancelled"),
    (GameStatus.UNKNOWN, "Unknown"),
])
def test_game_status_display_label(status, label):
    assert status.display_label == label


def test_game_status_value_strings():
    assert GameStatus.LIVE.value == "live"
    assert GameStatus.FINAL.value == "final"
    assert GameStatus.SCHEDULED.value == "scheduled"


# ── Game properties ───────────────────────────────────────────────

def test_game_display_matchup():
    g = make_game()
    assert g.display_matchup == "NYY @ BOS"


def test_game_display_time():
    g = make_game()
    assert g.display_time == "7:10 PM"


def test_game_display_score_with_scores():
    g = make_game(away_score=3, home_score=2)
    assert g.display_score == "3 - 2"


def test_game_display_score_zero_zero():
    g = make_game(away_score=0, home_score=0)
    assert g.display_score == "0 - 0"


def test_game_display_score_empty_when_no_scores():
    g = make_game()
    assert g.display_score == ""


def test_game_display_score_empty_when_only_one_score():
    # away has score but home doesn't — shouldn't partially display
    g = make_game()
    g.away_team.score = 3
    assert g.display_score == ""


def test_game_summary_no_score():
    g = make_game()
    summary = g.summary
    assert "NYY @ BOS" in summary
    assert "Scheduled" in summary
    assert "7:10 PM" in summary


def test_game_summary_with_live_score():
    g = make_game(status=GameStatus.LIVE, away_score=5, home_score=3)
    summary = g.summary
    assert "5 - 3" in summary
    assert "LIVE" in summary


def test_game_summary_no_score_field_when_none():
    g = make_game(status=GameStatus.SCHEDULED)
    # No scores → no score section in summary
    assert " - " not in g.summary


def test_game_extra_defaults_to_empty_dict():
    g = make_game()
    assert g.extra == {}


def test_team_score_defaults_to_none():
    t = Team(name="Yankees", abbreviation="NYY")
    assert t.score is None


def test_game_provider():
    g = make_game()
    assert g.provider == "mlb"


def test_game_venue():
    g = make_game()
    assert g.venue == "Fenway Park"
