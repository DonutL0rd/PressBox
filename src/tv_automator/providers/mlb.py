"""MLB.TV streaming provider — schedule data from the public Stats API."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")

import statsapi

from .base import Game, GameStatus, StreamingProvider, Team

log = logging.getLogger(__name__)

_STATUS_MAP: dict[str, GameStatus] = {
    "Pre-Game": GameStatus.PRE_GAME,
    "Warmup": GameStatus.PRE_GAME,
    "Scheduled": GameStatus.SCHEDULED,
    "In Progress": GameStatus.LIVE,
    "Live": GameStatus.LIVE,
    "Game Over": GameStatus.FINAL,
    "Final": GameStatus.FINAL,
    "Postponed": GameStatus.POSTPONED,
    "Cancelled": GameStatus.CANCELLED,
    "Suspended": GameStatus.POSTPONED,
    "Delayed": GameStatus.PRE_GAME,
    "Delayed Start": GameStatus.PRE_GAME,
}


MLB_TEAMS = [
    {"abbreviation": abbr, "name": name}
    for name, abbr in {
        "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
        "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
        "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
        "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
        "Colorado Rockies": "COL", "Detroit Tigers": "DET",
        "Houston Astros": "HOU", "Kansas City Royals": "KC",
        "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
        "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
        "Minnesota Twins": "MIN", "New York Mets": "NYM",
        "New York Yankees": "NYY", "Oakland Athletics": "OAK",
        "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
        "San Diego Padres": "SD", "San Francisco Giants": "SF",
        "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
        "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
        "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
    }.items()
]


class MLBProvider(StreamingProvider):
    """MLB.TV provider — fetches schedules from the public Stats API."""

    @property
    def name(self) -> str:
        return "mlb"

    @property
    def display_name(self) -> str:
        return "MLB.TV"

    async def get_schedule(self, date: datetime) -> list[Game]:
        date_str = date.strftime("%Y-%m-%d")
        loop = asyncio.get_event_loop()
        try:
            raw_games = await loop.run_in_executor(
                None, lambda: statsapi.schedule(date=date_str),
            )
        except Exception:
            log.exception("Failed to fetch MLB schedule for %s", date_str)
            return []

        games: list[Game] = []
        for g in raw_games:
            try:
                status = _STATUS_MAP.get(g.get("status", "Unknown"), GameStatus.UNKNOWN)
                game_datetime = datetime.fromisoformat(
                    g.get("game_datetime", date_str).replace("Z", "+00:00")
                )
                # Normalize to naive UTC first, then convert to Pacific
                if game_datetime.tzinfo is None:
                    game_datetime = game_datetime.replace(tzinfo=timezone.utc)
                game_datetime = game_datetime.astimezone(_PACIFIC)

                away_team = Team(
                    name=g.get("away_name", "Unknown"),
                    abbreviation=_team_abbrev(g.get("away_name", "")),
                    score=g.get("away_score"),
                )
                home_team = Team(
                    name=g.get("home_name", "Unknown"),
                    abbreviation=_team_abbrev(g.get("home_name", "")),
                    score=g.get("home_score"),
                )

                games.append(Game(
                    game_id=str(g.get("game_id", "")),
                    provider=self.name,
                    away_team=away_team,
                    home_team=home_team,
                    start_time=game_datetime,
                    status=status,
                    venue=g.get("venue_name", ""),
                    description=g.get("summary", ""),
                    extra={
                        "game_type": g.get("game_type", ""),
                        "series_status": g.get("series_status", ""),
                        "national_broadcasts": g.get("national_broadcasts", ""),
                        "away_probable_pitcher": g.get("away_probable_pitcher", ""),
                        "home_probable_pitcher": g.get("home_probable_pitcher", ""),
                        "current_inning": g.get("current_inning"),
                        "inning_state": g.get("inning_state", ""),
                    },
                ))
            except Exception:
                log.exception("Failed to parse game: %s", g)

        games.sort(key=lambda g: (
            0 if g.status == GameStatus.LIVE else
            1 if g.status == GameStatus.PRE_GAME else
            2 if g.status == GameStatus.SCHEDULED else
            3,
            g.start_time,
        ))
        return games

    async def get_game_status(self, game_id: str) -> GameStatus:
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None, lambda: statsapi.schedule(game_id=int(game_id)),
            )
            if data:
                return _STATUS_MAP.get(data[0].get("status", "Unknown"), GameStatus.UNKNOWN)
        except Exception:
            log.exception("Failed to get status for game %s", game_id)
        return GameStatus.UNKNOWN


def _team_abbrev(team_name: str) -> str:
    abbrevs: dict[str, str] = {
        "Arizona Diamondbacks": "ARI",
        "Atlanta Braves": "ATL",
        "Baltimore Orioles": "BAL",
        "Boston Red Sox": "BOS",
        "Chicago Cubs": "CHC",
        "Chicago White Sox": "CWS",
        "Cincinnati Reds": "CIN",
        "Cleveland Guardians": "CLE",
        "Colorado Rockies": "COL",
        "Detroit Tigers": "DET",
        "Houston Astros": "HOU",
        "Kansas City Royals": "KC",
        "Los Angeles Angels": "LAA",
        "Los Angeles Dodgers": "LAD",
        "Miami Marlins": "MIA",
        "Milwaukee Brewers": "MIL",
        "Minnesota Twins": "MIN",
        "New York Mets": "NYM",
        "New York Yankees": "NYY",
        "Oakland Athletics": "OAK",
        "Philadelphia Phillies": "PHI",
        "Pittsburgh Pirates": "PIT",
        "San Diego Padres": "SD",
        "San Francisco Giants": "SF",
        "Seattle Mariners": "SEA",
        "St. Louis Cardinals": "STL",
        "Tampa Bay Rays": "TB",
        "Texas Rangers": "TEX",
        "Toronto Blue Jays": "TOR",
        "Washington Nationals": "WSH",
    }
    return abbrevs.get(team_name, team_name[:3].upper())
