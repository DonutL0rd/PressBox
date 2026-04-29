"""Game scheduler — polls providers for schedule updates and triggers auto-start."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable, Awaitable

from tv_automator.config import Config
from tv_automator.providers.base import Game, GameStatus, StreamingProvider

log = logging.getLogger(__name__)

# Type alias for the callback when a game should auto-start
AutoStartCallback = Callable[[StreamingProvider, Game], Awaitable[None]]


class GameScheduler:
    """
    Periodically polls streaming providers for game schedule updates.

    Responsibilities:
    - Fetch and cache today's game schedule
    - Track game status transitions (scheduled → live → final)
    - Trigger auto-start for favorite teams when games go live
    - Provide the current schedule to the web dashboard
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._providers: dict[str, StreamingProvider] = {}
        self._schedules: dict[str, list[Game]] = {}  # provider_name → games
        self._poll_task: asyncio.Task | None = None
        self._auto_start_callback: AutoStartCallback | None = None
        self._auto_started_games: set[str] = set()  # game_ids we've already auto-started
        self._on_refresh: Callable[[], Awaitable[None]] | None = None

    # ── Provider management ─────────────────────────────────────

    def register_provider(self, provider: StreamingProvider) -> None:
        """Register a streaming provider."""
        self._providers[provider.name] = provider
        self._schedules[provider.name] = []
        log.info("Registered provider: %s", provider.display_name)

    def get_provider(self, name: str) -> StreamingProvider | None:
        return self._providers.get(name)

    @property
    def providers(self) -> dict[str, StreamingProvider]:
        return dict(self._providers)

    # ── Schedule access ─────────────────────────────────────────

    def get_all_games(self) -> list[Game]:
        """Get all games from all providers, sorted by start time."""
        all_games: list[Game] = []
        for games in self._schedules.values():
            all_games.extend(games)
        all_games.sort(key=lambda g: (
            0 if g.status == GameStatus.LIVE else
            1 if g.status == GameStatus.PRE_GAME else
            2 if g.status == GameStatus.SCHEDULED else
            3,
            g.start_time,
        ))
        return all_games

    def get_games_for_provider(self, provider_name: str) -> list[Game]:
        """Get games for a specific provider."""
        return list(self._schedules.get(provider_name, []))

    def get_live_games(self) -> list[Game]:
        """Get all currently live games."""
        return [g for g in self.get_all_games() if g.status == GameStatus.LIVE]

    def get_game_by_id(self, game_id: str) -> Game | None:
        """Find a game by its ID."""
        for games in self._schedules.values():
            for game in games:
                if game.game_id == game_id:
                    return game
        return None

    # ── Polling lifecycle ───────────────────────────────────────

    def set_auto_start_callback(self, callback: AutoStartCallback) -> None:
        """Set a callback to be invoked when a favorite team's game goes live."""
        self._auto_start_callback = callback

    def set_on_refresh(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Set a callback to be invoked after every schedule refresh."""
        self._on_refresh = callback

    async def start(self) -> None:
        """Start the polling loop."""
        log.info("Starting game scheduler (poll interval: %ds)", self._config.poll_interval)
        # Do an initial fetch
        await self.refresh()
        # Start the background polling loop
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the polling loop."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        log.info("Game scheduler stopped")

    async def refresh(self) -> None:
        """Manually refresh all schedules."""
        now = datetime.now()
        for name, provider in self._providers.items():
            try:
                games = await provider.get_schedule(now)
                self._schedules[name] = games
                log.info(
                    "Refreshed %s schedule: %d games",
                    provider.display_name,
                    len(games),
                )
            except Exception:
                log.exception("Failed to refresh %s schedule", name)
        if self._on_refresh:
            try:
                await self._on_refresh()
            except Exception:
                log.exception("on_refresh callback failed")

    # ── Internal polling ────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Background loop that periodically refreshes schedules."""
        while True:
            try:
                await asyncio.sleep(self._config.poll_interval)
                await self.refresh()
                await self._check_auto_start()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Error in scheduler poll loop")

    async def _check_auto_start(self) -> None:
        """Check if any favorite team games have gone live and should auto-start."""
        if not self._config.auto_start or not self._auto_start_callback:
            return

        favorite_teams = {t.upper() for t in self._config.favorite_teams}
        if not favorite_teams:
            return

        for game in self.get_live_games():
            if game.game_id in self._auto_started_games:
                continue

            # Check if either team is a favorite
            teams = {
                game.away_team.abbreviation.upper(),
                game.home_team.abbreviation.upper(),
            }
            if teams & favorite_teams:
                log.info(
                    "Auto-starting favorite team game: %s",
                    game.display_matchup,
                )
                self._auto_started_games.add(game.game_id)
                provider = self._providers.get(game.provider)
                if provider:
                    try:
                        await self._auto_start_callback(provider, game)
                    except Exception:
                        log.exception("Auto-start failed for %s", game.game_id)
