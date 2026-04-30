"""Tests for pitch_data.py — pure parsing functions for MLB live-feed data."""

from __future__ import annotations

import pytest

from tv_automator.web.pitch_data import (
    _get_due_up,
    _get_pitcher_summary,
    parse_batter_intel,
    parse_break_data,
    parse_innings,
    parse_pitches,
    parse_runners,
    parse_score,
)


# ── parse_pitches ─────────────────────────────────────────────────

def make_event(px=0.1, pz=2.5, is_pitch=True, code="FF", speed=92.5, call="Ball"):
    return {
        "isPitch": is_pitch,
        "pitchNumber": 1,
        "pitchData": {
            "coordinates": {"pX": px, "pZ": pz},
            "startSpeed": speed,
            "strikeZoneTop": 3.5,
            "strikeZoneBottom": 1.6,
        },
        "details": {
            "type": {"code": code, "description": "Four-Seam Fastball"},
            "description": "Ball",
            "call": {"description": call},
        },
    }


def test_parse_pitches_empty_events():
    pitches, zone_top, zone_bot = parse_pitches([])
    assert pitches == []
    assert zone_top == 3.4
    assert zone_bot == 1.6


def test_parse_pitches_skips_non_pitch_events():
    events = [
        make_event(),
        {"isPitch": False, "type": "pickoff"},
    ]
    pitches, _, _ = parse_pitches(events)
    assert len(pitches) == 1


def test_parse_pitches_skips_events_missing_coordinates():
    ev = make_event()
    ev["pitchData"]["coordinates"] = {}  # no pX/pZ
    pitches, _, _ = parse_pitches([ev])
    assert pitches == []


def test_parse_pitches_returns_correct_fields():
    pitches, _, _ = parse_pitches([make_event(px=0.5, pz=2.0, speed=94.1)])
    p = pitches[0]
    assert p["pX"] == 0.5
    assert p["pZ"] == 2.0
    assert p["speed_mph"] == 94.1
    assert p["type"] == "FF"
    assert p["pitchType"] == "Four-Seam Fastball"
    assert p["call"] == "Ball"


def test_parse_pitches_zone_from_last_pitch():
    events = [
        make_event(pz=2.0),
        make_event(pz=2.5),
    ]
    # Manually set different zone values on second event
    events[1]["pitchData"]["strikeZoneTop"] = 3.6
    events[1]["pitchData"]["strikeZoneBottom"] = 1.7

    pitches, zone_top, zone_bot = parse_pitches(events)
    assert zone_top == 3.6
    assert zone_bot == 1.7


def test_parse_pitches_multiple():
    events = [make_event(px=i * 0.1, pz=i * 0.2) for i in range(5)]
    pitches, _, _ = parse_pitches(events)
    assert len(pitches) == 5


# ── parse_runners ─────────────────────────────────────────────────

def test_parse_runners_empty_offense():
    assert parse_runners({}) == {"first": False, "second": False, "third": False}


def test_parse_runners_all_bases_loaded():
    linescore = {"offense": {"first": {}, "second": {}, "third": {}}}
    result = parse_runners(linescore)
    assert result == {"first": True, "second": True, "third": True}


def test_parse_runners_only_first():
    linescore = {"offense": {"first": {}}}
    result = parse_runners(linescore)
    assert result["first"] is True
    assert result["second"] is False
    assert result["third"] is False


def test_parse_runners_no_offense_key():
    result = parse_runners({"teams": {}})
    assert result == {"first": False, "second": False, "third": False}


# ── parse_score ───────────────────────────────────────────────────

def make_linescore(away_runs=3, home_runs=2):
    return {
        "teams": {
            "away": {"runs": away_runs},
            "home": {"runs": home_runs},
        }
    }


def make_game_data(away_abbr="NYY", home_abbr="BOS"):
    return {
        "teams": {
            "away": {"abbreviation": away_abbr},
            "home": {"abbreviation": home_abbr},
        }
    }


def test_parse_score_returns_correct_values():
    score = parse_score(make_linescore(3, 2), make_game_data("NYY", "BOS"))
    assert score == {"away": 3, "home": 2, "away_abbr": "NYY", "home_abbr": "BOS"}


def test_parse_score_defaults_to_zero():
    score = parse_score({}, {})
    assert score["away"] == 0
    assert score["home"] == 0
    assert score["away_abbr"] == ""
    assert score["home_abbr"] == ""


# ── parse_innings ─────────────────────────────────────────────────

def test_parse_innings_empty():
    assert parse_innings({}) == []


def test_parse_innings_returns_per_inning_runs():
    linescore = {
        "innings": [
            {"num": 1, "away": {"runs": 0}, "home": {"runs": 1}},
            {"num": 2, "away": {"runs": 2}, "home": {"runs": 0}},
        ]
    }
    innings = parse_innings(linescore)
    assert len(innings) == 2
    assert innings[0] == {"num": 1, "away": 0, "home": 1}
    assert innings[1] == {"num": 2, "away": 2, "home": 0}


def test_parse_innings_handles_missing_runs():
    linescore = {"innings": [{"num": 9, "away": {}, "home": {}}]}
    innings = parse_innings(linescore)
    assert innings[0]["away"] is None
    assert innings[0]["home"] is None


# ── parse_batter_intel ────────────────────────────────────────────

def make_boxscore(batter_id=101, ab=2, hits=1, hr=0, bb=0, avg=".300", obp=".350", slg=".450"):
    return {
        "teams": {
            "away": {
                "players": {
                    f"ID{batter_id}": {
                        "seasonStats": {
                            "batting": {"avg": avg, "obp": obp, "slg": slg, "homeRuns": hr}
                        },
                        "stats": {
                            "batting": {"atBats": ab, "hits": hits, "homeRuns": 0, "baseOnBalls": bb}
                        },
                    }
                }
            },
            "home": {"players": {}},
        }
    }


def test_parse_batter_intel_returns_none_when_no_batter():
    result, needs_fetch = parse_batter_intel(
        None, None, "", "Top", {}, None, None, False
    )
    assert result is None
    assert needs_fetch is False


def test_parse_batter_intel_is_new_when_batter_changed():
    intel, _ = parse_batter_intel(
        batter_id=101, pitcher_id=202, batter_name="Judge",
        inning_half="Top", boxscore=make_boxscore(101),
        last_batter_id=99,  # different
        cached_vs=None, has_cached_vs=False,
    )
    assert intel["is_new"] is True


def test_parse_batter_intel_not_new_same_batter():
    intel, _ = parse_batter_intel(
        batter_id=101, pitcher_id=202, batter_name="Judge",
        inning_half="Top", boxscore=make_boxscore(101),
        last_batter_id=101,  # same
        cached_vs=None, has_cached_vs=False,
    )
    assert intel["is_new"] is False


def test_parse_batter_intel_uses_cached_vs():
    vs = {"ab": 10, "h": 3, "hr": 1, "avg": ".300"}
    intel, needs_fetch = parse_batter_intel(
        batter_id=101, pitcher_id=202, batter_name="Judge",
        inning_half="Top", boxscore=make_boxscore(101),
        last_batter_id=None, cached_vs=vs, has_cached_vs=True,
    )
    assert intel["vs_pitcher"] == vs
    assert needs_fetch is False


def test_parse_batter_intel_requests_fetch_when_no_cache():
    _, needs_fetch = parse_batter_intel(
        batter_id=101, pitcher_id=202, batter_name="Judge",
        inning_half="Top", boxscore=make_boxscore(101),
        last_batter_id=None, cached_vs=None, has_cached_vs=False,
    )
    assert needs_fetch is True


def test_parse_batter_intel_no_fetch_when_no_pitcher():
    _, needs_fetch = parse_batter_intel(
        batter_id=101, pitcher_id=None, batter_name="Judge",
        inning_half="Top", boxscore=make_boxscore(101),
        last_batter_id=None, cached_vs=None, has_cached_vs=False,
    )
    assert needs_fetch is False


def test_parse_batter_intel_season_stats():
    intel, _ = parse_batter_intel(
        batter_id=101, pitcher_id=None, batter_name="Judge",
        inning_half="Top",
        boxscore=make_boxscore(101, avg=".310", obp=".400", slg=".600", hr=10),
        last_batter_id=None, cached_vs=None, has_cached_vs=False,
    )
    assert intel["season"]["avg"] == ".310"
    assert intel["season"]["obp"] == ".400"
    assert intel["season"]["hr"] == 10


def test_parse_batter_intel_home_team_bottom_inning():
    boxscore = {
        "teams": {
            "away": {"players": {}},
            "home": {
                "players": {
                    "ID55": {
                        "seasonStats": {"batting": {"avg": ".280", "obp": ".350", "slg": ".500", "homeRuns": 5}},
                        "stats": {"batting": {"atBats": 3, "hits": 1, "homeRuns": 0, "baseOnBalls": 0}},
                    }
                }
            },
        }
    }
    intel, _ = parse_batter_intel(
        batter_id=55, pitcher_id=None, batter_name="Betts",
        inning_half="Bottom",
        boxscore=boxscore,
        last_batter_id=None, cached_vs=None, has_cached_vs=False,
    )
    assert intel["season"]["avg"] == ".280"


# ── parse_break_data ──────────────────────────────────────────────

def test_parse_break_data_returns_none_when_not_break():
    result = parse_break_data("Top", {}, {}, {}, [], "Top 5")
    assert result is None


def test_parse_break_data_returns_none_when_bottom():
    result = parse_break_data("Bottom", {}, {}, {}, [], "Bottom 3")
    assert result is None


def test_parse_break_data_active_in_middle():
    linescore = {"teams": {"away": {"runs": 2}, "home": {"runs": 1}}}
    game_data = {"teams": {"away": {"abbreviation": "NYY"}, "home": {"abbreviation": "BOS"}}}
    result = parse_break_data("Middle", {}, linescore, game_data, [], "Middle 5")
    assert result is not None
    assert result["active"] is True
    assert result["game_score"]["away_r"] == 2
    assert result["game_score"]["home_r"] == 1
    assert result["game_score"]["away"] == "NYY"
    assert result["game_score"]["home"] == "BOS"


def test_parse_break_data_active_in_end():
    result = parse_break_data("End", {}, {}, {}, [], "End 9")
    assert result is not None
    assert result["active"] is True


def test_parse_break_data_includes_other_scores():
    scores = [{"away": "LAD", "home": "SF", "away_score": 3, "home_score": 1}]
    result = parse_break_data("Middle", {}, {}, {}, scores, "Middle 7")
    assert result["other_scores"] == scores


# ── _get_due_up ───────────────────────────────────────────────────

def make_boxscore_with_order(player_ids, ab_counts):
    players = {}
    for pid, ab in zip(player_ids, ab_counts):
        players[f"ID{pid}"] = {
            "person": {"fullName": f"Player {pid}"},
            "stats": {"batting": {"atBats": ab, "baseOnBalls": 0}},
            "seasonStats": {"batting": {"avg": ".250", "homeRuns": 5, "rbi": 20}},
        }
    return {"teams": {"away": {"battingOrder": player_ids, "players": players}, "home": {}}}


def test_get_due_up_empty_order():
    boxscore = {"teams": {"away": {"battingOrder": [], "players": {}}, "home": {}}}
    assert _get_due_up(boxscore, "End") == []


def test_get_due_up_returns_three_batters():
    pids = list(range(1, 10))
    abs_ = [1] * 9  # all have batted
    boxscore = make_boxscore_with_order(pids, abs_)
    due = _get_due_up(boxscore, "End")
    assert len(due) == 3


def test_get_due_up_wraps_around_lineup():
    pids = list(range(1, 10))
    # Last batter to bat was #8 (index 7), so due up: 9, 1, 2
    abs_ = [1, 1, 1, 1, 1, 1, 1, 1, 0]
    boxscore = make_boxscore_with_order(pids, abs_)
    due = _get_due_up(boxscore, "End")
    assert due[0]["name"] == "Player 9"
    assert due[1]["name"] == "Player 1"


# ── _get_pitcher_summary ──────────────────────────────────────────

def make_boxscore_with_pitcher(pitcher_id=42, ip="5.2", k=6, h=4, er=2):
    return {
        "teams": {
            "away": {
                "pitchers": [pitcher_id],
                "players": {
                    f"ID{pitcher_id}": {
                        "person": {"fullName": "Gerrit Cole"},
                        "stats": {
                            "pitching": {
                                "numberOfPitches": 87,
                                "strikes": 58,
                                "inningsPitched": ip,
                                "strikeOuts": k,
                                "hits": h,
                                "earnedRuns": er,
                            }
                        },
                    }
                },
            },
            "home": {"pitchers": [], "players": {}},
        }
    }


def test_get_pitcher_summary_returns_none_when_no_pitchers():
    boxscore = {"teams": {"away": {"pitchers": [], "players": {}}, "home": {}}}
    assert _get_pitcher_summary(boxscore, {}, "Middle") is None


def test_get_pitcher_summary_middle_uses_away_pitcher():
    boxscore = make_boxscore_with_pitcher(ip="5.2", k=6)
    result = _get_pitcher_summary(boxscore, {}, "Middle")
    assert result is not None
    assert result["name"] == "Gerrit Cole"
    assert result["ip"] == "5.2"
    assert result["k"] == 6


def test_get_pitcher_summary_end_uses_home_pitcher():
    boxscore = {
        "teams": {
            "away": {"pitchers": [], "players": {}},
            "home": {
                "pitchers": [77],
                "players": {
                    "ID77": {
                        "person": {"fullName": "Chris Sale"},
                        "stats": {"pitching": {"numberOfPitches": 60, "strikes": 40,
                                               "inningsPitched": "4.0", "strikeOuts": 5,
                                               "hits": 3, "earnedRuns": 1}},
                    }
                },
            },
        }
    }
    result = _get_pitcher_summary(boxscore, {}, "End")
    assert result["name"] == "Chris Sale"
