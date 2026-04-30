"""Pure parsing functions for MLB live-feed pitch data.

All functions take raw MLB Stats API JSON dicts and return plain Python
structures. No I/O, no side effects — fully unit-testable.
"""

from __future__ import annotations


def parse_pitches(play_events: list[dict]) -> tuple[list[dict], float, float]:
    """Extract pitch locations and metadata from a play's events.

    Returns:
        (pitches, zone_top, zone_bot) where zone values are from the last pitch.
    """
    pitches = []
    for ev in play_events:
        if not ev.get("isPitch"):
            continue
        pd_ev = ev.get("pitchData", {})
        coords = pd_ev.get("coordinates", {})
        px = coords.get("pX")
        pz = coords.get("pZ")
        if px is None or pz is None:
            continue
        pitches.append({
            "pX": px,
            "pZ": pz,
            "type": ev.get("details", {}).get("type", {}).get("code", ""),
            "description": ev.get("details", {}).get("description", ""),
            "speed": ev.get("pitchNumber", 0),
            "call": ev.get("details", {}).get("call", {}).get("description", ""),
            "pitchType": ev.get("details", {}).get("type", {}).get("description", ""),
            "speed_mph": pd_ev.get("startSpeed"),
            "zone_top": pd_ev.get("strikeZoneTop", 3.4),
            "zone_bot": pd_ev.get("strikeZoneBottom", 1.6),
        })

    zone_top, zone_bot = 3.4, 1.6
    if pitches:
        zone_top = pitches[-1].get("zone_top", 3.4)
        zone_bot = pitches[-1].get("zone_bot", 1.6)

    return pitches, zone_top, zone_bot


def parse_batter_intel(
    batter_id: int | None,
    pitcher_id: int | None,
    batter_name: str,
    inning_half: str,
    boxscore: dict,
    last_batter_id: int | None,
    cached_vs: dict | None,
    has_cached_vs: bool,
) -> tuple[dict | None, bool]:
    """Build the batter intel card from boxscore data.

    Args:
        cached_vs: The cached vs-pitcher stat dict (may be None if no matchup data).
        has_cached_vs: True if the cache has an entry for this matchup (even if None).

    Returns:
        (batter_intel dict or None, needs_vs_fetch)
        needs_vs_fetch is True when the caller should fire an async lookup.
    """
    if not batter_id:
        return None, False

    is_new = batter_id != last_batter_id
    bat_team = "away" if inning_half == "Top" else "home"
    bp = (
        boxscore.get("teams", {})
        .get(bat_team, {})
        .get("players", {})
        .get(f"ID{batter_id}", {})
    )
    season = bp.get("seasonStats", {}).get("batting", {})
    today = bp.get("stats", {}).get("batting", {})

    vs = cached_vs if has_cached_vs else None
    needs_fetch = not has_cached_vs and pitcher_id is not None

    return {
        "is_new": is_new,
        "name": batter_name,
        "season": {
            "avg": season.get("avg", ".000"),
            "obp": season.get("obp", ".000"),
            "slg": season.get("slg", ".000"),
            "hr": season.get("homeRuns", 0),
        },
        "today": {
            "ab": today.get("atBats", 0),
            "h": today.get("hits", 0),
            "hr": today.get("homeRuns", 0),
            "bb": today.get("baseOnBalls", 0),
        },
        "vs_pitcher": vs,
    }, needs_fetch


def parse_break_data(
    inning_state: str,
    boxscore: dict,
    linescore: dict,
    game_data: dict,
    other_scores: list[dict],
    inning_str: str,
) -> dict | None:
    """Build the between-innings break overlay data.

    Returns None if not currently in a break (Middle/End of inning).
    """
    if inning_state not in ("Middle", "End"):
        return None

    due_up = _get_due_up(boxscore, inning_state)
    pitcher_summary = _get_pitcher_summary(boxscore, linescore, inning_state)
    ls_teams = linescore.get("teams", {})
    gd = game_data.get("teams", {})

    return {
        "active": True,
        "other_scores": other_scores,
        "due_up": due_up,
        "pitcher": pitcher_summary,
        "game_score": {
            "away": gd.get("away", {}).get("abbreviation", ""),
            "home": gd.get("home", {}).get("abbreviation", ""),
            "away_r": ls_teams.get("away", {}).get("runs", 0),
            "home_r": ls_teams.get("home", {}).get("runs", 0),
        },
        "inning": inning_str,
    }


def parse_runners(linescore: dict) -> dict:
    """Return which bases are occupied."""
    offense = linescore.get("offense", {})
    return {
        "first": "first" in offense,
        "second": "second" in offense,
        "third": "third" in offense,
    }


def parse_score(linescore: dict, game_data: dict) -> dict:
    """Return the current score and team abbreviations."""
    ls_teams = linescore.get("teams", {})
    gd_teams = game_data.get("teams", {})
    return {
        "away": ls_teams.get("away", {}).get("runs", 0),
        "home": ls_teams.get("home", {}).get("runs", 0),
        "away_abbr": gd_teams.get("away", {}).get("abbreviation", ""),
        "home_abbr": gd_teams.get("home", {}).get("abbreviation", ""),
    }


def parse_innings(linescore: dict) -> list[dict]:
    """Return per-inning run totals."""
    return [
        {
            "num": inn.get("num"),
            "away": inn.get("away", {}).get("runs"),
            "home": inn.get("home", {}).get("runs"),
        }
        for inn in linescore.get("innings", [])
    ]


# ── Helpers (also used by app.py directly) ────────────────────────

def _get_due_up(boxscore: dict, inning_state: str) -> list[dict]:
    team_key = "home" if inning_state == "Middle" else "away"
    team = boxscore.get("teams", {}).get(team_key, {})
    order = team.get("battingOrder", [])
    players = team.get("players", {})
    if not order:
        return []

    last_idx = 0
    for i, pid in enumerate(order):
        pd = players.get(f"ID{pid}", {})
        ab = pd.get("stats", {}).get("batting", {}).get("atBats", 0)
        bb = pd.get("stats", {}).get("batting", {}).get("baseOnBalls", 0)
        if ab > 0 or bb > 0:
            last_idx = i

    due = []
    for offset in range(1, 4):
        idx = (last_idx + offset) % len(order)
        pid = order[idx]
        pd = players.get(f"ID{pid}", {})
        season = pd.get("seasonStats", {}).get("batting", {})
        due.append({
            "name": pd.get("person", {}).get("fullName", ""),
            "avg": season.get("avg", ".000"),
            "hr": season.get("homeRuns", 0),
            "rbi": season.get("rbi", 0),
        })
    return due


def _get_pitcher_summary(
    boxscore: dict, linescore: dict, inning_state: str
) -> dict | None:
    team_key = "away" if inning_state == "Middle" else "home"
    team = boxscore.get("teams", {}).get(team_key, {})
    pitcher_ids = team.get("pitchers", [])
    players = team.get("players", {})
    if not pitcher_ids:
        return None
    pid = pitcher_ids[-1]
    pd = players.get(f"ID{pid}", {})
    stats = pd.get("stats", {}).get("pitching", {})
    return {
        "name": pd.get("person", {}).get("fullName", ""),
        "pitches": stats.get("numberOfPitches", 0),
        "strikes": stats.get("strikes", 0),
        "ip": stats.get("inningsPitched", "0.0"),
        "k": stats.get("strikeOuts", 0),
        "h": stats.get("hits", 0),
        "er": stats.get("earnedRuns", 0),
    }
