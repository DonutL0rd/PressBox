"""FastAPI web dashboard for TV-Automator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from urllib.parse import urljoin

import xml.etree.ElementTree as ET

import httpx

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles

from tv_automator.automator.browser_control import BrowserController
from tv_automator.automator.cec_control import CECController
from tv_automator.config import Config
from tv_automator.providers.base import Game, GameStatus, StreamingProvider
from tv_automator.providers.mlb import MLBProvider, MLB_TEAMS
from tv_automator.providers.mlb_session import MLBSession, StreamInfo
from tv_automator.scheduler.game_scheduler import GameScheduler

log = logging.getLogger(__name__)

# ── Templates ───────────────────────────────────────────────────
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_PLAYER_HTML = (_TEMPLATE_DIR / "player.html").read_text()
_SCREENSAVER_HTML = (_TEMPLATE_DIR / "screensaver.html").read_text()
_YOUTUBE_HTML = (_TEMPLATE_DIR / "youtube.html").read_text()

# ── App state ────────────────────────────────────────────────────

_config: Config
_browser: BrowserController
_cec: CECController
_mlb: MLBProvider
_session: MLBSession
_scheduler: GameScheduler

_now_playing_game_id: str | None = None
_now_playing_feed: str = "HOME"
_stream_info: StreamInfo | None = None
_youtube_mode: bool = False
_player_levels: list[dict] = []   # quality levels reported by player after manifest parse
_player_command: dict | None = None  # pending command for player (consumed on read)
_youtube_video_id: str | None = None   # currently playing YouTube video ID
_autoplay_queue: dict | None = None  # {game_id, feed, display_matchup, display_time}
_play_lock: asyncio.Lock
_music_lock: asyncio.Lock
_heartbeat_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None
_expiry_task: asyncio.Task | None = None
_progress_task: asyncio.Task | None = None   # periodic YouTube progress saver
_browser_started_at: float = 0  # monotonic time
CHROME_RECYCLE_HOURS = 8  # restart Chrome after this many hours of idle

# ── Watch history ─────────────────────────────────────────────────
# Stored as list (newest first) in /data/watch_history.json.
# Kept as dict[video_id → entry] in memory for O(1) lookup.
_watch_history: dict[str, dict] = {}

# WebSocket clients
_ws_clients: set[WebSocket] = set()
_last_games_hash: str = ""

# ── Batter intel / between-innings caches ────────────────────────
_last_batter_id: int | None = None
_batter_vs_pitcher_cache: dict[tuple[int, int], dict | None] = {}
_other_scores_cache: list[dict] = []
_other_scores_cache_time: float = 0
OTHER_SCORES_TTL = 30  # seconds

# ── Shared httpx client (reuse connections) ────────────────────
_http_client: httpx.AsyncClient | None = None

# ── Music broadcast deduplication ──────────────────────────────
_last_music_broadcast: str = ""

# ── Suggested YouTube channels ──────────────────────────────────
# channel_id → display name
SUGGESTED_CHANNELS: dict[str, str] = {
    "UCsBjURrPoezykLs9EqgamOA": "Fireship",
    "UCYO_jab_esuFRV4b17AJtAw": "3Blue1Brown",
    "UCBJycsmduvYEL83R_U4JriQ": "MKBHD",
    "UCKelCK4ZaO6HeEI1KQjqzWA": "AI Daily Brief",
}
_suggested_cache: dict[str, list[dict]] = {}
_suggested_cache_time: float = 0
SUGGESTED_CACHE_TTL = 1800  # 30 minutes


# ── Watch history helpers ────────────────────────────────────────

def _history_path() -> Path:
    return Path(_config.data_dir) / "watch_history.json"


def _load_history() -> None:
    global _watch_history
    try:
        data = json.loads(_history_path().read_text())
        _watch_history = {e["video_id"]: e for e in data}
    except Exception:
        _watch_history = {}


def _save_history() -> None:
    entries = sorted(_watch_history.values(), key=lambda e: e.get("last_watched", ""), reverse=True)
    try:
        _history_path().write_text(json.dumps(entries, indent=2))
    except Exception:
        log.exception("Failed to save watch history")


async def _fetch_video_info(video_id: str) -> dict:
    """Fetch title and channel from YouTube oEmbed (no API key needed)."""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url)
            if r.status_code == 200:
                d = r.json()
                return {"title": d.get("title", ""), "channel": d.get("author_name", "")}
    except Exception:
        pass
    return {"title": "", "channel": ""}


def _history_record_start(video_id: str, info: dict) -> None:
    """Create or refresh a history entry when playback begins."""
    now = datetime.now(timezone.utc).isoformat()
    if video_id in _watch_history:
        _watch_history[video_id]["last_watched"] = now
        if info.get("title"):
            _watch_history[video_id]["title"] = info["title"]
            _watch_history[video_id]["channel"] = info.get("channel", "")
    else:
        _watch_history[video_id] = {
            "video_id": video_id,
            "title": info.get("title", ""),
            "channel": info.get("channel", ""),
            "duration": 0.0,
            "position": 0.0,
            "completed": False,
            "first_watched": now,
            "last_watched": now,
        }
    _save_history()


async def _save_current_progress(completed: bool = False) -> None:
    """Read position from the browser and persist it."""
    global _watch_history
    if not _youtube_video_id:
        return
    raw = await _browser.evaluate("window.ytGetState ? window.ytGetState() : null")
    if not raw:
        return
    try:
        state = json.loads(raw)
    except Exception:
        return
    position = state.get("currentTime", 0)
    duration = state.get("duration", 0)
    if _youtube_video_id not in _watch_history:
        return
    entry = _watch_history[_youtube_video_id]
    entry["position"] = round(position, 1)
    if duration > 0:
        entry["duration"] = round(duration, 1)
    if completed or (duration > 0 and position / duration >= 0.90):
        entry["completed"] = True
        entry["position"] = 0.0   # reset so replay starts from beginning
    entry["last_watched"] = datetime.now(timezone.utc).isoformat()
    _save_history()
    log.debug("Progress saved: %s %.0fs/%.0fs completed=%s",
              _youtube_video_id, position, duration, entry["completed"])


async def _progress_save_loop() -> None:
    """Save YouTube playback position to disk every 30 seconds."""
    while True:
        await asyncio.sleep(30)
        try:
            await _save_current_progress()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Progress save error")


def _start_progress_task() -> None:
    global _progress_task
    _stop_progress_task()
    _progress_task = asyncio.create_task(_progress_save_loop())


def _stop_progress_task() -> None:
    global _progress_task
    if _progress_task and not _progress_task.done():
        _progress_task.cancel()
    _progress_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _browser, _cec, _mlb, _session, _scheduler, _play_lock, _music_lock, _watchdog_task
    _config = Config()
    _load_history()
    _browser = BrowserController(_config)
    _cec = CECController(enabled=_config.cec.get("enabled", False))
    _mlb = MLBProvider()
    _session = MLBSession()
    _scheduler = GameScheduler(_config)
    _scheduler.register_provider(_mlb)
    _scheduler.set_on_refresh(_on_schedule_refresh)
    _play_lock = asyncio.Lock()
    _music_lock = asyncio.Lock()

    global _browser_started_at
    try:
        await _browser.start()
        _browser_started_at = time.monotonic()
        log.info("Browser started")
    except Exception:
        log.exception("Browser failed to start — check DISPLAY / X11")

    creds = _config.mlb_credentials
    if creds:
        username, password = creds
        log.info("MLB credentials found — logging in via API...")
        ok = await _session.login(username, password)
        if ok:
            log.info("MLB.TV login successful")
        else:
            log.error("MLB.TV login failed — check MLB_USERNAME / MLB_PASSWORD")
    else:
        log.warning("No MLB credentials — set MLB_USERNAME and MLB_PASSWORD in .env")

    # Register auto-start callback
    _scheduler.set_auto_start_callback(_auto_start_game)

    await _scheduler.start()

    # Start the watchdog
    _watchdog_task = asyncio.create_task(_watchdog_loop())

    # Shared httpx client for external API calls (connection pooling)
    global _http_client
    _http_client = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=10))

    # Navigate to screensaver once the server is ready (retry — browser starts before uvicorn binds)
    if _browser.is_running:
        asyncio.create_task(_initial_navigate())

    yield

    # Shutdown
    if _watchdog_task:
        _watchdog_task.cancel()
    _stop_heartbeat()
    _stop_expiry_timer()
    _stop_progress_task()
    await _scheduler.stop()
    await _session.close()
    await _browser.stop()
    if _http_client:
        await _http_client.aclose()


app = FastAPI(lifespan=lifespan)


# ── Background tasks ────────────────────────────────────────────

async def _initial_navigate() -> None:
    """Navigate to the screensaver after uvicorn finishes binding."""
    for _ in range(20):
        await asyncio.sleep(0.5)
        if await _browser.navigate("http://127.0.0.1:5000/screensaver"):
            return
    log.warning("Initial screensaver navigation failed after retries")


async def _heartbeat_loop() -> None:
    """Send periodic heartbeats to keep the MLB stream alive."""
    while True:
        info = _stream_info  # capture reference; may be replaced by reconnect
        if not info or not info.heartbeat_url:
            return
        await asyncio.sleep(info.heartbeat_interval)
        ok = await _session.send_heartbeat(info.heartbeat_url)
        if ok:
            log.debug("Heartbeat OK")
        else:
            log.warning("Heartbeat failed — stream may drop soon")
            await _broadcast({
                "type": "error",
                "code": "heartbeat_failed",
                "message": "Stream heartbeat failed — video may freeze soon",
            })


def _start_heartbeat() -> None:
    global _heartbeat_task
    _stop_heartbeat()
    if _stream_info and _stream_info.heartbeat_url:
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
        log.info("Heartbeat started (every %ds)", _stream_info.heartbeat_interval)


def _stop_heartbeat() -> None:
    global _heartbeat_task
    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()
        log.info("Heartbeat stopped")
    _heartbeat_task = None


async def _expiry_refresh_loop() -> None:
    """Proactively refresh the stream URL before it expires."""
    while _stream_info and _stream_info.expiration:
        now = datetime.now(timezone.utc)
        # Refresh 2 minutes before expiry
        remaining = (_stream_info.expiration - now).total_seconds() - 120
        if remaining > 0:
            log.info("Stream expires in %.0fs — will refresh in %.0fs", remaining + 120, remaining)
            await asyncio.sleep(remaining)
        # Time to refresh
        log.info("Proactively refreshing stream before expiry...")
        await _do_reconnect()
        return  # _do_reconnect starts a new expiry timer


def _start_expiry_timer() -> None:
    global _expiry_task
    _stop_expiry_timer()
    if _stream_info and _stream_info.expiration:
        _expiry_task = asyncio.create_task(_expiry_refresh_loop())


def _stop_expiry_timer() -> None:
    global _expiry_task
    if _expiry_task and not _expiry_task.done():
        _expiry_task.cancel()
    _expiry_task = None


async def _watchdog_loop() -> None:
    """Monitor browser and stream health, auto-recover on failure."""
    global _browser_started_at
    while True:
        await asyncio.sleep(30)
        try:
            # Check browser health — restart if crashed
            if not _browser.is_healthy:
                log.warning("Watchdog: browser unhealthy — restarting...")
                if await _browser.restart():
                    _browser_started_at = time.monotonic()
                    if _now_playing_game_id:
                        log.info("Watchdog: reconnecting stream after browser restart...")
                        await _do_reconnect()
                    else:
                        await _browser.navigate("http://127.0.0.1:5000/screensaver")

            # Chrome memory leak prevention — recycle if idle for too long
            elif (
                _browser.is_running
                and not _now_playing_game_id
                and _browser_started_at
                and (time.monotonic() - _browser_started_at) > CHROME_RECYCLE_HOURS * 3600
            ):
                log.info("Watchdog: recycling Chrome after %dh idle", CHROME_RECYCLE_HOURS)
                if await _browser.restart():
                    _browser_started_at = time.monotonic()
                    await _browser.navigate("http://127.0.0.1:5000/screensaver")

            # Proactively refresh auth before expiry
            if _session._username and not _session.is_authenticated:
                log.info("Watchdog: token expiring — refreshing...")
                await _session.ensure_authenticated()

        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Watchdog error")


# ── Auto-start callback ──────────────────────────────────────

async def _auto_start_game(provider: StreamingProvider, game: Game) -> None:
    """Called by the scheduler when a favorite team's game goes live."""
    async with _play_lock:
        if _now_playing_game_id:
            log.info("Auto-start skipped — already playing %s", _now_playing_game_id)
            return

        # Determine feed: use the favorite team's feed
        fav_teams = {t.upper() for t in _config.favorite_teams}
        if game.home_team.abbreviation.upper() in fav_teams:
            feed = "HOME"
        elif game.away_team.abbreviation.upper() in fav_teams:
            feed = "AWAY"
        else:
            feed = "HOME"

        log.info("Auto-starting: %s (feed=%s)", game.display_matchup, feed)
        try:
            await _do_play(game.game_id, feed)
        except Exception:
            log.exception("Auto-start failed for %s", game.game_id)


# ── WebSocket broadcast ──────────────────────────────────────

async def _broadcast(message: dict) -> None:
    """Send a message to all connected WebSocket clients."""
    if not _ws_clients:
        return
    data = json.dumps(message)
    dead: list[WebSocket] = []
    for client in _ws_clients:
        try:
            await client.send_text(data)
        except Exception:
            dead.append(client)
    for client in dead:
        _ws_clients.discard(client)


async def _broadcast_status() -> None:
    """Broadcast current playback status to all WS clients."""
    await _broadcast({
        "type": "status",
        "now_playing_game_id": _now_playing_game_id,
        "youtube_mode": _youtube_mode,
        "youtube_video_id": _youtube_video_id,
        "authenticated": _session.is_authenticated,
        "browser_running": _browser.is_running,
        "heartbeat_active": _heartbeat_task is not None and not _heartbeat_task.done(),
    })


async def _broadcast_autoplay_state() -> None:
    """Broadcast current autoplay queue state to all WS clients."""
    if _autoplay_queue:
        await _broadcast({"type": "autoplay", "queued": True, **_autoplay_queue})
    else:
        await _broadcast({"type": "autoplay", "queued": False, "game_id": None})


async def _broadcast_settings() -> None:
    """Broadcast current settings to all WS clients."""
    data = await get_settings()
    await _broadcast({"type": "settings", **data})


async def _broadcast_music_status() -> None:
    """Broadcast current music playback state to all WS clients (skips if unchanged)."""
    global _last_music_broadcast
    data = await music_status()
    # Only broadcast position changes at reduced fidelity to avoid flooding
    key = f"{data.get('playing')}|{data.get('paused')}|{data.get('song', {}) or ''}|{int(data.get('position', 0))}"
    if key == _last_music_broadcast:
        return
    _last_music_broadcast = key
    await _broadcast({"type": "music", **data})


async def _broadcast_volume() -> None:
    """Broadcast current volume state to all WS clients."""
    try:
        data = await get_volume()
        await _broadcast({"type": "volume", **data})
    except Exception:
        pass  # volume control may be unavailable


async def _broadcast_music_queue() -> None:
    """Broadcast current music queue to all WS clients."""
    await _broadcast({"type": "queue", "songs": _music_queue, "index": _music_queue_index})


async def _auto_start_queued(queue_entry: dict) -> None:
    """Auto-start a specifically queued game once it goes live."""
    async with _play_lock:
        if _now_playing_game_id:
            log.info("Queued auto-start skipped — already playing %s", _now_playing_game_id)
            return
        try:
            await _do_play(queue_entry["game_id"], queue_entry.get("feed", "HOME"))
        except Exception:
            log.exception("Queued auto-start failed for %s", queue_entry["game_id"])


async def _on_schedule_refresh() -> None:
    """Called after every scheduler refresh — broadcast if games changed."""
    global _last_games_hash, _autoplay_queue
    games = _scheduler.get_games_for_provider("mlb")

    # Check if a specifically queued game has gone live
    if _autoplay_queue and not _now_playing_game_id:
        queued_game = _scheduler.get_game_by_id(_autoplay_queue["game_id"])
        if queued_game and queued_game.status == GameStatus.LIVE:
            log.info("Queued game went live: %s — auto-starting", queued_game.display_matchup)
            q = _autoplay_queue
            _autoplay_queue = None
            asyncio.create_task(_auto_start_queued(q))
            await _broadcast_autoplay_state()

    game_dicts = [_game_to_dict(g) for g in games]
    h = hashlib.md5(json.dumps(game_dicts, default=str).encode()).hexdigest()
    if h != _last_games_hash:
        _last_games_hash = h
        await _broadcast({"type": "games", "games": game_dicts})


# ── Play / reconnect logic ──────────────────────────────────────

async def _get_condensed_url(game_id: str) -> str | None:
    """Fetch condensed game video URL from the public MLB Stats API content endpoint."""
    url = f"https://statsapi.mlb.com/api/v1/game/{game_id}/content"
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                log.warning("Content endpoint returned %d for game %s", resp.status_code, game_id)
                return None
            data = resp.json()
        except Exception:
            log.exception("Failed to fetch content for game %s", game_id)
            return None

    # Search highlights for the condensed game video
    items = data.get("highlights", {}).get("highlights", {}).get("items", [])
    for item in items:
        headline = (item.get("headline") or "").lower()
        slug = (item.get("slug") or "").lower()
        keywords = item.get("keywordsAll") or []
        keyword_vals = {k.get("type", ""): k.get("value", "") for k in keywords}

        is_condensed = (
            "condensed" in headline
            or "condensed" in slug
            or keyword_vals.get("taxonomy") == "condensedGame"
            or "cg" in slug
        )
        if not is_condensed:
            continue

        playbacks = item.get("playbacks") or []
        # Prefer HLS, then highest quality mp4
        for pb in playbacks:
            if "hls" in (pb.get("name") or "").lower():
                log.info("Found condensed HLS for game %s: %s", game_id, pb["url"][:80])
                return pb["url"]
        for pb in playbacks:
            name = (pb.get("name") or "").lower()
            if "mp4avc" in name or "highbit" in name:
                log.info("Found condensed MP4 for game %s: %s", game_id, pb["url"][:80])
                return pb["url"]
        # Any playback at all
        if playbacks:
            log.info("Found condensed playback for game %s: %s", game_id, playbacks[0].get("url", "")[:80])
            return playbacks[0].get("url")

    log.warning("No condensed game found for game %s (%d highlight items checked)", game_id, len(items))
    return None


async def _do_play_condensed(game_id: str) -> StreamInfo:
    """Play a condensed game replay from the public MLB CDN (no auth needed)."""
    global _now_playing_game_id, _now_playing_feed, _stream_info, _browser_started_at

    url = await _get_condensed_url(game_id)
    if not url:
        raise HTTPException(404, "Condensed game not available — it may take a few hours after the game ends")

    info = StreamInfo(url=url, direct=True)
    _stream_info = info
    _now_playing_game_id = game_id
    _now_playing_feed = "CONDENSED"
    # No heartbeat or expiry needed for public VOD

    if _cec.enabled:
        await _cec.power_on()
        await _cec.set_active_source()

    if not _browser.is_running:
        await _browser.start()
        _browser_started_at = time.monotonic()

    ok = await _browser.navigate("http://127.0.0.1:5000/player")
    if not ok:
        raise HTTPException(503, "Failed to navigate browser to player")

    await _broadcast_status()
    return info


async def _do_play(game_id: str, feed: str) -> StreamInfo:
    """Get a stream and navigate Chrome to the player. Returns StreamInfo."""
    global _now_playing_game_id, _now_playing_feed, _stream_info

    if not await _session.ensure_authenticated():
        raise HTTPException(401, "Not authenticated — check MLB_USERNAME / MLB_PASSWORD in .env")

    info = await _session.get_stream_info(game_id, feed_type=feed)
    if not info:
        raise HTTPException(502, "Could not get stream URL — game may not be available yet")

    _stream_info = info
    _now_playing_game_id = game_id
    _now_playing_feed = feed
    _start_heartbeat()
    _start_expiry_timer()

    # CEC: power on TV
    if _cec.enabled:
        await _cec.power_on()
        await _cec.set_active_source()

    ok = await _browser.navigate("http://127.0.0.1:5000/player")
    if not ok:
        raise HTTPException(503, "Failed to navigate browser to player")

    await _broadcast_status()
    return info


async def _do_reconnect(schedule_retry: bool = True) -> StreamInfo | None:
    """Get a fresh stream URL for the current game and reload the player."""
    global _stream_info

    if not _now_playing_game_id:
        return None

    log.info("Reconnecting stream for game %s (feed=%s)...",
             _now_playing_game_id, _now_playing_feed)

    _stop_heartbeat()
    _stop_expiry_timer()

    try:
        if _now_playing_feed == "CONDENSED":
            url = await _get_condensed_url(_now_playing_game_id)
            if not url:
                log.error("Reconnect failed — condensed game not available")
                await _broadcast({
                    "type": "error",
                    "code": "stream_error",
                    "message": "Stream reconnect failed — condensed game not available",
                })
                if schedule_retry:
                    asyncio.create_task(_reconnect_with_retry())
                return None
            info = StreamInfo(url=url, direct=True)
        else:
            info = await _session.get_stream_info(_now_playing_game_id, _now_playing_feed)
            if not info:
                log.error("Reconnect failed — no stream URL")
                await _broadcast({
                    "type": "error",
                    "code": "stream_error",
                    "message": "Stream reconnect failed — retrying…",
                })
                if schedule_retry:
                    asyncio.create_task(_reconnect_with_retry())
                return None
            _start_heartbeat()
            _start_expiry_timer()

        _stream_info = info
        await _browser.navigate("http://127.0.0.1:5000/player")
        log.info("Reconnected successfully")
        return info
    except Exception:
        log.exception("Reconnect failed")
        await _broadcast({
            "type": "error",
            "code": "stream_error",
            "message": "Stream reconnect failed — retrying…",
        })
        if schedule_retry:
            asyncio.create_task(_reconnect_with_retry())
        return None


async def _reconnect_with_retry() -> None:
    """Retry _do_reconnect up to 3 times with 30-second delays between attempts."""
    for attempt in range(1, 4):
        await asyncio.sleep(30)
        if not _now_playing_game_id:
            return  # User stopped playback while waiting
        log.info("Reconnect retry %d/3 for game %s...", attempt, _now_playing_game_id)
        result = await _do_reconnect(schedule_retry=False)
        if result:
            await _broadcast({
                "type": "info",
                "code": "stream_recovered",
                "message": "Stream reconnected successfully",
            })
            return
    # All retries exhausted
    log.error("Stream reconnect gave up after 3 attempts for game %s", _now_playing_game_id)
    await _broadcast({
        "type": "error",
        "code": "stream_dead",
        "message": "Stream could not reconnect — stop and restart playback",
    })


async def _do_stop() -> None:
    global _now_playing_game_id, _now_playing_feed, _stream_info, _youtube_mode, _youtube_video_id, _player_levels, _player_command
    _stop_heartbeat()
    _stop_expiry_timer()
    _stop_progress_task()
    if _youtube_mode:
        await _save_current_progress()
    _now_playing_game_id = None
    _now_playing_feed = "HOME"
    _stream_info = None
    _youtube_mode = False
    _youtube_video_id = None
    _player_levels = []
    _player_command = None

    # Navigate to screensaver instead of blank
    if _browser.is_running:
        await _browser.navigate("http://127.0.0.1:5000/screensaver")

    # CEC: power off TV
    if _cec.enabled and _config.cec.get("power_off_on_stop", True):
        await _cec.power_off()

    await _broadcast_status()


async def _stop_music_internal() -> None:
    """Stop music playback and clear the queue."""
    global _music_queue_index, _music_watcher_task
    if _music_watcher_task and not _music_watcher_task.done():
        _music_watcher_task.cancel()
        _music_watcher_task = None
    async with _music_lock:
        await _mpv_stop()
        _music_queue.clear()
        _music_queue_index = -1


async def _stop_video_for_music() -> None:
    """Stop any active video playback (game or YouTube) so music can take over.
    Navigates the browser to the screensaver. Call within _play_lock."""
    global _now_playing_game_id, _now_playing_feed, _stream_info, _youtube_mode, _youtube_video_id, _player_levels, _player_command
    was_playing = bool(_now_playing_game_id or _youtube_mode)
    _stop_heartbeat()
    _stop_expiry_timer()
    _stop_progress_task()
    if _youtube_mode and _youtube_video_id:
        await _save_current_progress()
    _now_playing_game_id = None
    _now_playing_feed = "HOME"
    _stream_info = None
    _youtube_mode = False
    _youtube_video_id = None
    _player_levels = []
    _player_command = None
    if was_playing and _browser.is_running:
        await _browser.navigate("http://127.0.0.1:5000/screensaver")


# ── Helpers ──────────────────────────────────────────────────────

def _game_to_dict(game: Game) -> dict:
    return {
        "game_id": game.game_id,
        "provider": game.provider,
        "away_team": {
            "name": game.away_team.name,
            "abbreviation": game.away_team.abbreviation,
            "score": game.away_team.score,
        },
        "home_team": {
            "name": game.home_team.name,
            "abbreviation": game.home_team.abbreviation,
            "score": game.home_team.score,
        },
        "start_time": game.start_time.isoformat(),
        "display_time": game.display_time,
        "display_matchup": game.display_matchup,
        "display_score": game.display_score,
        "status": game.status.value,
        "status_label": game.status.display_label,
        "is_watchable": game.status.is_watchable,
        "venue": game.venue,
        "extra": game.extra,
    }


# ── Routes ───────────────────────────────────────────────────────

# Mount React static assets if built
_FRONTEND_DIST = _TEMPLATE_DIR.parent / "frontend" / "dist"
if (_FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

@app.get("/", response_class=FileResponse)
@app.get("/mlb", response_class=FileResponse)
@app.get("/youtube", response_class=FileResponse)
@app.get("/settings", response_class=FileResponse)
@app.get("/music", response_class=FileResponse)
async def dashboard():
    index_file = _FRONTEND_DIST / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("React frontend not built. Run 'npm run build' in src/tv_automator/web/frontend.")


@app.get("/api/games")
async def get_games(date: str | None = None):
    target = datetime.fromisoformat(date) if date else datetime.now()
    # Use scheduler's cache for today, fetch directly for other dates
    if target.date() == datetime.now().date():
        games = _scheduler.get_games_for_provider("mlb")
        if games:
            return [_game_to_dict(g) for g in games]
    games = await _mlb.get_schedule(target)
    return [_game_to_dict(g) for g in games]


@app.post("/api/play/{game_id}")
async def play_game(game_id: str, date: str | None = None, feed: str = "HOME"):
    if not _browser.is_running:
        raise HTTPException(503, "Browser not running — check DISPLAY / X11")

    async with _play_lock:
        await _stop_music_internal()
        _stop_heartbeat()
        if feed.upper() == "CONDENSED":
            info = await _do_play_condensed(game_id)
        else:
            info = await _do_play(game_id, feed.upper())
        return {"success": True, "feed": feed.upper()}


@app.post("/api/stop")
async def stop_playback():
    async with _play_lock:
        await _do_stop()
    return {"success": True}


@app.post("/api/video-ended")
async def video_ended():
    """Called by the player when a VOD (condensed game) finishes playing.
    Clears playback state and returns to the screensaver."""
    async with _play_lock:
        await _do_stop()
    log.info("Video ended — returned to screensaver")
    return {"success": True}


def _extract_youtube_id(url: str) -> str | None:
    """Extract a YouTube video ID from common URL formats."""
    m = re.search(
        r'(?:youtube\.com/watch\?(?:[^&]*&)*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)'
        r'([a-zA-Z0-9_-]{11})',
        url,
    )
    return m.group(1) if m else None


@app.post("/api/youtube")
async def play_youtube(body: dict):
    """Navigate the TV browser to a YouTube video."""
    global _youtube_mode, _youtube_video_id, _now_playing_game_id, _now_playing_feed, _stream_info
    url = body.get("url", "").strip()
    video_id = _extract_youtube_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL — paste a youtube.com or youtu.be link")

    if not _browser.is_running:
        raise HTTPException(503, "Browser not running — check DISPLAY / X11")

    # Build nav URL — support resume position
    resume_pos = body.get("resume_position", 0)
    nav_url = f"http://127.0.0.1:5000/tv/youtube?v={video_id}"
    if resume_pos and resume_pos > 5:
        nav_url += f"&t={int(resume_pos)}"

    async with _play_lock:
        # Save progress for any currently playing YouTube video before switching
        if _youtube_mode and _youtube_video_id:
            await _save_current_progress()
        _stop_progress_task()

        # Stop any active MLB stream (without navigating away)
        if _now_playing_game_id:
            _stop_heartbeat()
            _stop_expiry_timer()
            _now_playing_game_id = None
            _now_playing_feed = "HOME"
            _stream_info = None

        await _stop_music_internal()
        _youtube_mode = True
        _youtube_video_id = video_id
        await _browser.navigate(nav_url)

    # Fetch video info and record in history (async, don't block response)
    async def _record():
        info = await _fetch_video_info(video_id)
        _history_record_start(video_id, info)
        _start_progress_task()

    asyncio.create_task(_record())
    await _broadcast_status()
    log.info("YouTube: playing video %s (resume=%.0fs)", video_id, resume_pos)
    return {"success": True, "video_id": video_id}


@app.post("/api/screensaver")
async def show_screensaver(body: dict | None = None):
    """Navigate the TV browser to the screensaver."""
    global _youtube_mode, _youtube_video_id
    completed = (body or {}).get("completed", False)
    async with _play_lock:
        if _now_playing_game_id:
            await _do_stop()  # also navigates to screensaver
        else:
            if _youtube_mode:
                _stop_progress_task()
                await _save_current_progress(completed=completed)
            _youtube_mode = False
            _youtube_video_id = None
            if _browser.is_running:
                await _browser.navigate("http://127.0.0.1:5000/screensaver")
            await _broadcast_status()
    return {"success": True}


@app.get("/api/youtube/history")
async def get_youtube_history():
    entries = sorted(_watch_history.values(), key=lambda e: e.get("last_watched", ""), reverse=True)
    return entries


@app.delete("/api/youtube/history/{video_id}")
async def delete_youtube_history(video_id: str):
    _watch_history.pop(video_id, None)
    _save_history()
    return {"success": True}


@app.get("/api/youtube/suggested")
async def get_suggested_videos():
    """Return recent videos from curated YouTube channels via public RSS feeds."""
    global _suggested_cache, _suggested_cache_time
    now = time.monotonic()
    if _suggested_cache and (now - _suggested_cache_time) < SUGGESTED_CACHE_TTL:
        return _suggested_cache

    results: dict[str, list[dict]] = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for channel_id, channel_name in SUGGESTED_CHANNELS.items():
            try:
                url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
                resp = await client.get(url)
                if resp.status_code != 200:
                    results[channel_name] = []
                    continue
                root = ET.fromstring(resp.text)
                ns = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
                entries = root.findall("atom:entry", ns)
                videos = []
                for entry in entries:
                    vid_id_el = entry.find("atom:id", ns)
                    title_el = entry.find("atom:title", ns)
                    published_el = entry.find("atom:published", ns)
                    thumb_el = entry.find("media:group/media:thumbnail", ns)
                    vid_text = vid_id_el.text if vid_id_el is not None else ""
                    video_id = vid_text.split(":")[-1] if vid_text else ""
                    title = title_el.text if title_el is not None else ""
                    # Skip Shorts
                    if "#shorts" in title.lower() or "#short" in title.lower():
                        continue
                    videos.append({
                        "video_id": video_id,
                        "title": title,
                        "published": published_el.text if published_el is not None else "",
                        "thumbnail": thumb_el.get("url", "") if thumb_el is not None else "",
                        "channel": channel_name,
                    })
                    if len(videos) >= 6:
                        break
                results[channel_name] = videos
            except Exception:
                log.exception("Failed to fetch RSS for %s", channel_name)
                results[channel_name] = []

    _suggested_cache = results
    _suggested_cache_time = now
    return results


@app.get("/api/youtube/state")
async def youtube_state():
    """Return current YouTube player state (time, duration, paused, volume)."""
    if not _youtube_mode:
        return {"state": -1, "currentTime": 0, "duration": 0, "volume": 100, "muted": False}
    raw = await _browser.evaluate("window.ytGetState ? window.ytGetState() : null")
    if raw:
        return json.loads(raw)
    return {"state": -1, "currentTime": 0, "duration": 0, "volume": 100, "muted": False}


@app.post("/api/youtube/command")
async def youtube_command(body: dict):
    """Send a playback command to the YouTube player running in Chrome."""
    if not _youtube_mode:
        raise HTTPException(400, "YouTube mode not active")
    cmd = body.get("cmd")
    simple = {"play": "window.ytPlay()", "pause": "window.ytPause()",
               "mute": "window.ytMute()", "unmute": "window.ytUnmute()"}
    if cmd in simple:
        await _browser.evaluate(simple[cmd])
    elif cmd == "seek":
        t = float(body.get("time", 0))
        await _browser.evaluate(f"window.ytSeek({t})")
    elif cmd == "volume":
        v = max(0, min(100, int(body.get("volume", 50))))
        await _browser.evaluate(f"window.ytSetVolume({v})")
    elif cmd == "speed":
        allowed = {0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0}
        r = float(body.get("rate", 1.0))
        if r not in allowed:
            r = 1.0
        await _browser.evaluate(f"window.ytSetSpeed({r})")
    elif cmd == "cc":
        on = bool(body.get("enabled", False))
        await _browser.evaluate(f"window.ytSetCC({'true' if on else 'false'})")
    else:
        raise HTTPException(400, f"Unknown command: {cmd}")
    return {"success": True}


async def _fetch_other_scores() -> list[dict]:
    """Cached fetch of other live game scores from the MLB schedule endpoint."""
    global _other_scores_cache, _other_scores_cache_time
    now = time.monotonic()
    if _other_scores_cache and (now - _other_scores_cache_time) < OTHER_SCORES_TTL:
        return _other_scores_cache
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=linescore"
        client = _get_http_client()
        resp = await client.get(url)
        if resp.status_code != 200:
            return _other_scores_cache
        data = resp.json()
        scores = []
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                gid = str(g.get("gamePk", ""))
                if gid == _now_playing_game_id:
                    continue
                ls = g.get("linescore", {})
                teams_g = g.get("teams", {})
                status = g.get("status", {})
                state = status.get("detailedState", "")
                inn = ls.get("currentInningOrdinal", "")
                half = ls.get("inningHalf", "")
                scores.append({
                    "away": teams_g.get("away", {}).get("team", {}).get("abbreviation", ""),
                    "home": teams_g.get("home", {}).get("team", {}).get("abbreviation", ""),
                    "away_score": teams_g.get("away", {}).get("score", 0),
                    "home_score": teams_g.get("home", {}).get("score", 0),
                    "inning": f"{half} {inn}" if half and inn else "",
                    "state": state,
                })
        _other_scores_cache = scores
        _other_scores_cache_time = now
        return scores
    except Exception:
        log.debug("Failed to fetch other scores", exc_info=True)
        return _other_scores_cache


def _get_due_up(boxscore: dict, inning_state: str) -> list[dict]:
    """Get the next 3 batters due up based on batting order and inning state."""
    # Middle = home bats next, End = away bats next
    team_key = "home" if inning_state == "Middle" else "away"
    team = boxscore.get("teams", {}).get(team_key, {})
    order = team.get("battingOrder", [])
    players = team.get("players", {})
    if not order:
        return []

    # Find who batted last by checking game stats for at-bats
    # Simple approach: walk order, find last batter with at-bats, return next 3
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


def _get_pitcher_summary(boxscore: dict, linescore: dict, inning_state: str) -> dict | None:
    """Get the current pitcher's stats for the break overlay."""
    # Middle = home was pitching (top just ended), End = away was pitching (bottom just ended)
    team_key = "away" if inning_state == "Middle" else "home"
    team = boxscore.get("teams", {}).get(team_key, {})
    pitcher_ids = team.get("pitchers", [])
    players = team.get("players", {})
    if not pitcher_ids:
        return None
    # Current pitcher is the last one in the list
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


def _get_http_client() -> httpx.AsyncClient:
    """Return the shared httpx client, or create a fallback."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=10))
    return _http_client


@app.get("/api/pitches")
async def get_pitches(game_id: str | None = None):
    """Return pitch data, batter intel, and between-innings break data."""
    global _last_batter_id

    empty = {"pitches": [], "batter": "", "pitcher": "", "count": "", "outs": 0,
             "inning": "", "batter_intel": None, "break_data": None}

    target_id = game_id or _now_playing_game_id
    if not target_id:
        # Fallback: pick the first live game from the scheduler
        live_games = [g for g in _scheduler.get_games_for_provider("mlb") if g.status == GameStatus.LIVE]
        if live_games:
            target_id = live_games[0].game_id
        else:
            return empty

    try:
        url = f"https://statsapi.mlb.com/api/v1.1/game/{target_id}/feed/live"
        client = _get_http_client()
        resp = await client.get(url)
        if resp.status_code != 200:
            return empty
        data = resp.json()

        live = data.get("liveData", {})
        linescore = live.get("linescore", {})
        boxscore = live.get("boxscore", {})
        plays = live.get("plays", {})
        current = plays.get("currentPlay", {})
        matchup = current.get("matchup", {})

        batter_name = matchup.get("batter", {}).get("fullName", "")
        pitcher_name = matchup.get("pitcher", {}).get("fullName", "")
        batter_id = matchup.get("batter", {}).get("id")
        pitcher_id = matchup.get("pitcher", {}).get("id")

        count_data = current.get("count", {})
        balls = count_data.get("balls", 0)
        strikes = count_data.get("strikes", 0)
        outs = count_data.get("outs", 0)
        count_str = f"{balls}-{strikes}"

        inning_half = linescore.get("inningHalf", "")
        inning_num = linescore.get("currentInning", "")
        inning_str = f"{inning_half} {inning_num}" if inning_half else ""
        inning_state = linescore.get("inningState", "")  # Top, Middle, Bottom, End

        # ── Pitches ─────────────────────────────────────
        events = current.get("playEvents", [])
        pitches = []
        for ev in events:
            if not ev.get("isPitch"):
                continue
            pd_ev = ev.get("pitchData", {})
            coords = pd_ev.get("coordinates", {})
            px = coords.get("pX")
            pz = coords.get("pZ")
            if px is None or pz is None:
                continue
            pitches.append({
                "pX": px, "pZ": pz,
                "type": ev.get("details", {}).get("type", {}).get("code", ""),
                "description": ev.get("details", {}).get("description", ""),
                "speed": ev.get("pitchNumber", 0),
                "call": ev.get("details", {}).get("call", {}).get("description", ""),
                "pitchType": ev.get("details", {}).get("type", {}).get("description", ""),
                "speed_mph": pd_ev.get("startSpeed"),
                "zone_top": pd_ev.get("strikeZoneTop", 3.4),
                "zone_bot": pd_ev.get("strikeZoneBottom", 1.6),
            })

        # ── Zone dimensions (from latest pitch, or defaults) ──
        zone_top = 3.4
        zone_bot = 1.6
        if pitches:
            zone_top = pitches[-1].get("zone_top", 3.4)
            zone_bot = pitches[-1].get("zone_bot", 1.6)

        # ── Batter intel ────────────────────────────────
        batter_intel = None
        if batter_id:
            is_new = batter_id != _last_batter_id
            _last_batter_id = batter_id

            # Determine batter's team
            bat_team = "away" if inning_half == "Top" else "home"
            bp = boxscore.get("teams", {}).get(bat_team, {}).get("players", {}).get(f"ID{batter_id}", {})
            season = bp.get("seasonStats", {}).get("batting", {})
            today = bp.get("stats", {}).get("batting", {})

            # vs pitcher (cached, non-blocking)
            vs = None
            cache_key = (batter_id, pitcher_id) if pitcher_id else None
            if cache_key and cache_key in _batter_vs_pitcher_cache:
                vs = _batter_vs_pitcher_cache[cache_key]
            elif cache_key:
                # Fire background fetch, return null this poll
                async def _fetch_vs(bid, pid, key):
                    try:
                        vurl = (f"https://statsapi.mlb.com/api/v1/people/{bid}/stats"
                                f"?stats=vsPlayer&opposingPlayerId={pid}&group=hitting")
                        async with httpx.AsyncClient(timeout=6) as c:
                            r = await c.get(vurl)
                            if r.status_code == 200:
                                splits = r.json().get("stats", [{}])[0].get("splits", [])
                                if splits:
                                    s = splits[0].get("stat", {})
                                    _batter_vs_pitcher_cache[key] = {
                                        "ab": s.get("atBats", 0), "h": s.get("hits", 0),
                                        "hr": s.get("homeRuns", 0), "avg": s.get("avg", ".000"),
                                    }
                                else:
                                    _batter_vs_pitcher_cache[key] = None
                    except Exception:
                        _batter_vs_pitcher_cache[key] = None
                asyncio.create_task(_fetch_vs(batter_id, pitcher_id, cache_key))

            batter_intel = {
                "is_new": is_new,
                "name": batter_name,
                "season": {
                    "avg": season.get("avg", ".000"), "obp": season.get("obp", ".000"),
                    "slg": season.get("slg", ".000"), "hr": season.get("homeRuns", 0),
                },
                "today": {
                    "ab": today.get("atBats", 0), "h": today.get("hits", 0),
                    "hr": today.get("homeRuns", 0), "bb": today.get("baseOnBalls", 0),
                },
                "vs_pitcher": vs,
            }

        # ── Between-innings break data ──────────────────
        break_data = None
        if inning_state in ("Middle", "End"):
            other_scores = await _fetch_other_scores()
            due_up = _get_due_up(boxscore, inning_state)
            pitcher_summary = _get_pitcher_summary(boxscore, linescore, inning_state)
            # Game score for context
            ls_teams = linescore.get("teams", {})
            gd = data.get("gameData", {}).get("teams", {})
            break_data = {
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

        # ── Runners ─────────────────────────────────────
        offense = linescore.get("offense", {})
        runners = {
            "first": "first" in offense,
            "second": "second" in offense,
            "third": "third" in offense
        }

        # ── Teams & Score ───────────────────────────────
        ls_teams = linescore.get("teams", {})
        gd_teams = data.get("gameData", {}).get("teams", {})
        score = {
            "away": ls_teams.get("away", {}).get("runs", 0),
            "home": ls_teams.get("home", {}).get("runs", 0),
            "away_abbr": gd_teams.get("away", {}).get("abbreviation", ""),
            "home_abbr": gd_teams.get("home", {}).get("abbreviation", ""),
        }

        # ── Simplified Linescore ────────────────────────
        innings_list = []
        for inn in linescore.get("innings", []):
            innings_list.append({
                "num": inn.get("num"),
                "away": inn.get("away", {}).get("runs"),
                "home": inn.get("home", {}).get("runs"),
            })

        return {
            "game_id": target_id,
            "pitches": pitches,
            "batter": batter_name,
            "pitcher": pitcher_name,
            "count": count_str,
            "balls": balls,
            "strikes": strikes,
            "outs": outs,
            "inning": inning_str,
            "inning_state": inning_state,
            "runners": runners,
            "score": score,
            "linescore": innings_list,
            "zone_top": zone_top,
            "zone_bot": zone_bot,
            "batter_intel": batter_intel,
            "break_data": break_data,
        }
    except Exception:
        log.exception("Failed to fetch pitch data")
        return empty


def _extract_pitcher_stats(team_data: dict) -> list[dict]:
    pitchers = team_data.get("pitchers", [])
    players = team_data.get("players", {})
    result = []
    for pid in pitchers:
        pd = players.get(f"ID{pid}", {})
        stats = pd.get("stats", {}).get("pitching", {})
        if not stats:
            continue
        result.append({
            "name": pd.get("person", {}).get("fullName", ""),
            "ip": stats.get("inningsPitched", "0.0"),
            "h": stats.get("hits", 0),
            "r": stats.get("runs", 0),
            "er": stats.get("earnedRuns", 0),
            "bb": stats.get("baseOnBalls", 0),
            "k": stats.get("strikeOuts", 0),
            "pitches": stats.get("numberOfPitches", 0),
        })
    return result


@app.get("/api/game/{game_id}/stats")
async def get_game_stats(game_id: str):
    """Return rich stats for a game: linescore, win probability, spray chart, scoring plays, pitchers."""
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "MLB Stats API error")
        data = resp.json()

    game_data = data.get("gameData", {})
    live = data.get("liveData", {})
    linescore = live.get("linescore", {})
    boxscore = live.get("boxscore", {})
    plays = live.get("plays", {})

    # Game info
    teams_gd = game_data.get("teams", {})
    info = {
        "away_name": teams_gd.get("away", {}).get("name", ""),
        "away_abbr": teams_gd.get("away", {}).get("abbreviation", ""),
        "home_name": teams_gd.get("home", {}).get("name", ""),
        "home_abbr": teams_gd.get("home", {}).get("abbreviation", ""),
        "venue": game_data.get("venue", {}).get("name", ""),
        "date": game_data.get("datetime", {}).get("originalDate", ""),
        "status": game_data.get("status", {}).get("detailedState", ""),
    }

    # Linescore
    innings = []
    for inn in linescore.get("innings", []):
        innings.append({
            "num": inn.get("num", ""),
            "away_r": inn.get("away", {}).get("runs", ""),
            "away_h": inn.get("away", {}).get("hits", ""),
            "away_e": inn.get("away", {}).get("errors", ""),
            "home_r": inn.get("home", {}).get("runs", ""),
            "home_h": inn.get("home", {}).get("hits", ""),
            "home_e": inn.get("home", {}).get("errors", ""),
        })
    ls_teams = linescore.get("teams", {})
    away_totals = {k: ls_teams.get("away", {}).get(k, 0) for k in ("runs", "hits", "errors", "leftOnBase")}
    home_totals = {k: ls_teams.get("home", {}).get(k, 0) for k in ("runs", "hits", "errors", "leftOnBase")}

    # Win probability per play
    all_plays = plays.get("allPlays", [])
    win_prob = []
    for p in all_plays:
        hwp = p.get("contextMetrics", {}).get("homeWinProbability")
        ab = p.get("about", {}).get("atBatIndex")
        if hwp is not None and ab is not None:
            win_prob.append({"ab": ab, "hwp": round(hwp, 1)})

    # Hit spray chart
    hits = []
    for p in all_plays:
        event = p.get("result", {}).get("event", "")
        if not event:
            continue
        hd = p.get("hitData", {})
        coords = hd.get("coordinates", {})
        cx = coords.get("coordX")
        cy = coords.get("coordY")
        if cx is None or cy is None:
            continue
        hits.append({
            "x": cx,
            "y": cy,
            "event": event,
            "batter": p.get("matchup", {}).get("batter", {}).get("fullName", ""),
            "exitVelo": hd.get("launchSpeed"),
            "angle": hd.get("launchAngle"),
            "distance": hd.get("totalDistance"),
        })

    # Scoring plays
    scoring_indices = plays.get("scoringPlays", [])
    scoring_plays_out = []
    for idx in scoring_indices:
        if idx >= len(all_plays):
            continue
        p = all_plays[idx]
        res = p.get("result", {})
        about = p.get("about", {})
        scoring_plays_out.append({
            "inning": about.get("inning", ""),
            "half": about.get("halfInning", ""),
            "desc": res.get("description", ""),
            "away": res.get("awayScore", 0),
            "home": res.get("homeScore", 0),
        })

    # Pitcher and batting stats from boxscore
    teams_bs = boxscore.get("teams", {})
    away_pitchers = _extract_pitcher_stats(teams_bs.get("away", {}))
    home_pitchers = _extract_pitcher_stats(teams_bs.get("home", {}))

    def batting_totals(team_data):
        s = team_data.get("teamStats", {}).get("batting", {})
        return {k: s.get(k, 0) for k in ("atBats", "runs", "hits", "homeRuns", "strikeOuts", "baseOnBalls", "leftOnBase")}

    return {
        "info": info,
        "linescore": {"innings": innings, "away": away_totals, "home": home_totals},
        "win_prob": win_prob,
        "hits": hits,
        "scoring_plays": scoring_plays_out,
        "away_pitchers": away_pitchers,
        "home_pitchers": home_pitchers,
        "away_batting": batting_totals(teams_bs.get("away", {})),
        "home_batting": batting_totals(teams_bs.get("home", {})),
    }


@app.post("/api/reconnect")
async def reconnect():
    """Get a fresh stream URL and reload the player. Called by player on errors."""
    async with _play_lock:
        info = await _do_reconnect()
        if info:
            return {"success": True, "url": info.url}
        raise HTTPException(502, "Reconnect failed")


@app.get("/api/status")
async def get_status():
    return {
        "now_playing_game_id": _now_playing_game_id,
        "now_playing_feed": _now_playing_feed,
        "youtube_mode": _youtube_mode,
        "authenticated": _session.is_authenticated,
        "browser_running": _browser.is_running,
        "heartbeat_active": _heartbeat_task is not None and not _heartbeat_task.done(),
    }


@app.get("/api/stream")
async def get_stream():
    if not _stream_info:
        raise HTTPException(404, "No stream active")
    if _stream_info.direct:
        return {"url": _stream_info.url}
    # Return proxied URL to avoid CORS issues
    return {"url": "/hls/master.m3u8"}


@app.get("/hls/{path:path}")
async def hls_proxy(path: str):
    """Proxy HLS requests to MLB CDN to avoid CORS blocks in the browser."""
    if not _stream_info:
        raise HTTPException(404, "No stream active")

    # Build the upstream URL: master.m3u8 → the stream URL itself,
    # anything else → relative to the stream base URL
    stream_url = _stream_info.url
    base_url = stream_url.rsplit("/", 1)[0] + "/"

    if path == "master.m3u8":
        upstream = stream_url
    else:
        upstream = urljoin(base_url, path)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            resp = await client.get(upstream)
        except Exception:
            log.exception("HLS proxy fetch failed: %s", path)
            raise HTTPException(502, "Upstream fetch failed")

    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, "Upstream error")

    content = resp.content
    ct = resp.headers.get("content-type", "application/octet-stream")

    # Rewrite .m3u8 playlists so relative URLs also go through our proxy
    if path.endswith(".m3u8") or "mpegurl" in ct:
        text = content.decode()
        rewritten_lines = []
        for line in text.splitlines():
            if line and not line.startswith("#"):
                # Relative segment/playlist URL → proxy through /hls/
                rewritten_lines.append("/hls/" + line)
            else:
                # Rewrite key URIs too
                if 'URI="' in line and not 'URI="http' in line:
                    line = line.replace('URI="', 'URI="/hls/')
                rewritten_lines.append(line)
        content = "\n".join(rewritten_lines).encode()
        ct = "application/vnd.apple.mpegurl"

    return Response(content=content, media_type=ct)


# ── Player quality control ──────────────────────────────────────

@app.post("/api/player/levels")
async def post_player_levels(body: dict):
    """Player reports available HLS quality levels after manifest parse."""
    global _player_levels
    _player_levels = body.get("levels", [])
    return {"ok": True}


@app.get("/api/player/levels")
async def get_player_levels():
    """Dashboard reads available quality levels for the current stream."""
    return {"levels": _player_levels}


@app.post("/api/player/command")
async def post_player_command(body: dict):
    """Dashboard sends a command to the player (e.g. quality change)."""
    global _player_command
    _player_command = body
    await _broadcast({"type": "player_command", **body})
    return {"ok": True}


@app.get("/api/player/command")
async def get_player_command():
    """Player polls for a pending command. Clears after read."""
    global _player_command
    cmd = _player_command
    _player_command = None
    return cmd or {}


# ── Volume ──────────────────────────────────────────────────────

@app.get("/api/volume")
async def get_volume():
    """Get current system volume (0-100) and mute state."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "get-sink-volume", "@DEFAULT_SINK@",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        m = re.search(r"(\d+)%", stdout.decode())
        volume = int(m.group(1)) if m else 0

        proc2 = await asyncio.create_subprocess_exec(
            "pactl", "get-sink-mute", "@DEFAULT_SINK@",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await proc2.communicate()
        muted = "yes" in stdout2.decode().lower()

        return {"volume": volume, "muted": muted}
    except Exception:
        log.exception("Failed to get volume")
        raise HTTPException(500, "Volume control unavailable")


@app.post("/api/volume")
async def set_volume(level: int | None = None, mute: bool | None = None):
    """Set system volume (0-100) and/or mute state."""
    try:
        if level is not None:
            level = max(0, min(100, level))
            proc = await asyncio.create_subprocess_exec(
                "pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        if mute is not None:
            proc = await asyncio.create_subprocess_exec(
                "pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if mute else "0",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        result = await get_volume()
        await _broadcast_volume()
        return result
    except Exception:
        log.exception("Failed to set volume")
        raise HTTPException(500, "Volume control unavailable")


# ── Favorites & Settings ────────────────────────────────────────

@app.get("/api/teams")
async def get_teams():
    """Return all MLB teams."""
    return MLB_TEAMS


@app.get("/api/favorites")
async def get_favorites():
    return {"teams": _config.favorite_teams}


@app.post("/api/favorites")
async def set_favorites(body: dict):
    teams = body.get("teams", [])
    _config.update_nested("providers", "mlb", "favorite_teams", value=teams)
    _config.save_user_config()
    log.info("Favorites updated: %s", teams)
    return {"teams": teams}


@app.get("/api/settings")
async def get_settings():
    overlay = _config.get("overlay", {})
    sched = _config.scheduler
    display = _config.display
    return {
        # Account
        "mlb_username": _config.mlb_username or "",
        "mlb_authenticated": _session.is_authenticated,
        # Playback
        "auto_start": _config.auto_start,
        "default_feed": _config.get("providers", {}).get("mlb", {}).get("default_feed", "HOME"),
        # Overlay
        "strike_zone_enabled": overlay.get("strike_zone_enabled", True),
        "strike_zone_size": overlay.get("strike_zone_size", "medium"),
        "batter_intel_enabled": overlay.get("batter_intel_enabled", True),
        "between_innings_enabled": overlay.get("between_innings_enabled", True),
        "overlay_delay": overlay.get("overlay_delay", 2),
        # Display
        "resolution": display.get("resolution", "1920x1080"),
        "fullscreen": display.get("fullscreen", True),
        # Scheduler
        "poll_interval": sched.get("poll_interval", 60),
        "pre_game_minutes": sched.get("pre_game_minutes", 5),
        # CEC
        "cec_enabled": _config.cec.get("enabled", False),
        "cec_power_off_on_stop": _config.cec.get("power_off_on_stop", True),
        # YouTube channels
        "suggested_channels": {cid: name for cid, name in SUGGESTED_CHANNELS.items()},
        # Screensaver
        "screensaver_music_size": _config.get("screensaver", {}).get("music_size", "medium"),
        "dvd_bounce": _config.get("screensaver", {}).get("dvd_bounce", False),
        # Navidrome
        "navidrome_server_url": os.getenv("NAVIDROME_URL") or _config.get("navidrome", {}).get("server_url", ""),
        "navidrome_username": os.getenv("NAVIDROME_USERNAME") or _config.get("navidrome", {}).get("username", ""),
    }


@app.get("/api/autoplay")
async def get_autoplay():
    """Get the currently queued auto-play game."""
    if not _autoplay_queue:
        return {"queued": False, "game_id": None, "feed": None}
    return {"queued": True, **_autoplay_queue}


@app.post("/api/autoplay")
async def set_autoplay(body: dict):
    """Queue a specific game to auto-play when it goes live. Send {} or {game_id: null} to clear."""
    global _autoplay_queue
    game_id = body.get("game_id")
    if not game_id:
        _autoplay_queue = None
        await _broadcast_autoplay_state()
        return {"queued": False}
    feed = body.get("feed", "HOME").upper()
    game = _scheduler.get_game_by_id(game_id)
    _autoplay_queue = {
        "game_id": game_id,
        "feed": feed,
        "display_matchup": game.display_matchup if game else game_id,
        "display_time": game.display_time if game else "",
    }
    await _broadcast_autoplay_state()
    return {"queued": True, **_autoplay_queue}


@app.post("/api/settings")
async def update_settings(body: dict):
    # Playback
    if "auto_start" in body:
        _config.update_nested("providers", "mlb", "auto_start", value=body["auto_start"])
    if "default_feed" in body:
        feed = body["default_feed"].upper() if body["default_feed"] in ("HOME", "AWAY") else "HOME"
        _config.update_nested("providers", "mlb", "default_feed", value=feed)
    # Overlay
    if "strike_zone_enabled" in body:
        _config.update_nested("overlay", "strike_zone_enabled", value=body["strike_zone_enabled"])
    if "strike_zone_size" in body:
        allowed = ("small", "medium", "large")
        sz = body["strike_zone_size"] if body["strike_zone_size"] in allowed else "medium"
        _config.update_nested("overlay", "strike_zone_size", value=sz)
    if "batter_intel_enabled" in body:
        _config.update_nested("overlay", "batter_intel_enabled", value=body["batter_intel_enabled"])
    if "between_innings_enabled" in body:
        _config.update_nested("overlay", "between_innings_enabled", value=body["between_innings_enabled"])
    if "overlay_delay" in body:
        val = max(0, min(15, float(body["overlay_delay"])))
        _config.update_nested("overlay", "overlay_delay", value=val)
    # Display
    if "resolution" in body:
        _config.update_nested("display", "resolution", value=body["resolution"])
    if "fullscreen" in body:
        _config.update_nested("display", "fullscreen", value=body["fullscreen"])
    # Scheduler
    if "poll_interval" in body:
        val = max(15, min(300, int(body["poll_interval"])))
        _config.update_nested("scheduler", "poll_interval", value=val)
    if "pre_game_minutes" in body:
        val = max(0, min(30, int(body["pre_game_minutes"])))
        _config.update_nested("scheduler", "pre_game_minutes", value=val)
    # CEC
    if "cec_enabled" in body:
        _config.update_nested("cec", "enabled", value=body["cec_enabled"])
        _cec._enabled = body["cec_enabled"]
    if "cec_power_off_on_stop" in body:
        _config.update_nested("cec", "power_off_on_stop", value=body["cec_power_off_on_stop"])
    # YouTube channels
    if "suggested_channels" in body:
        SUGGESTED_CHANNELS.clear()
        SUGGESTED_CHANNELS.update(body["suggested_channels"])
        _config.update_nested("youtube", "suggested_channels", value=body["suggested_channels"])
    # Screensaver
    if "screensaver_music_size" in body:
        allowed = ("small", "medium", "large")
        sz = body["screensaver_music_size"] if body["screensaver_music_size"] in allowed else "medium"
        _config.update_nested("screensaver", "music_size", value=sz)
    if "dvd_bounce" in body:
        _config.update_nested("screensaver", "dvd_bounce", value=bool(body["dvd_bounce"]))
    _config.save_user_config()
    log.info("Settings updated: %s", {k: v for k, v in body.items() if k != "mlb_password"})
    await _broadcast_settings()
    return await get_settings()


@app.post("/api/settings/credentials")
async def update_credentials(body: dict):
    """Update MLB.TV credentials and re-authenticate."""
    username = body.get("mlb_username", "").strip()
    password = body.get("mlb_password", "").strip()
    if not username or not password:
        raise HTTPException(400, "Username and password are required")

    # Attempt login BEFORE persisting — we don't want to save credentials
    # that we know are invalid, since the startup flow would retry them forever.
    ok = await _session.login(username, password)

    if ok:
        _config.update_nested("providers", "mlb", "username", value=username)
        _config.update_nested("providers", "mlb", "password", value=password)
        _config.save_user_config()
        log.info("Credentials updated and login successful for %s", username)
        # Refresh the schedule right away so the Dashboard sees games immediately
        # instead of waiting for the next poll cycle.
        asyncio.create_task(_scheduler.refresh())
        await _broadcast_status()
        return {"success": True, "authenticated": True}

    log.warning("Login failed for %s — credentials not saved", username)
    await _broadcast_status()
    return {"success": False, "authenticated": False, "error": "Login failed — check username/password"}


# ── Screen power ────────────────────────────────────────────────

# ── CEC ─────────────────────────────────────────────────────────

@app.get("/api/cec/status")
async def cec_status():
    available = await _cec.is_available()
    return {"available": available, "enabled": _cec.enabled}


@app.post("/api/cec/{action}")
async def cec_action(action: str):
    if action == "on":
        ok = await _cec.power_on()
        if ok:
            await _cec.set_active_source()
    elif action == "off":
        ok = await _cec.power_off()
    else:
        raise HTTPException(400, "Invalid action — use 'on' or 'off'")
    return {"success": ok}


# ── Navidrome / Music ──────────────────────────────────────────

# Server-side music playback via mpv + PulseAudio.
# The dashboard is a remote control — audio plays on the server, not the browser.

_mpv_proc: asyncio.subprocess.Process | None = None
_mpv_socket = "/tmp/mpv-music.sock"
_music_queue: list[dict] = []       # [{id, title, artist, albumId, duration}, ...]
_music_queue_index: int = -1
_music_shuffle: bool = False
_music_repeat: str = "off"          # off, all, one
_music_shuffle_order: list[int] = []


def _subsonic_params() -> dict[str, str] | None:
    """Build Subsonic API auth query params. Returns None if not configured."""
    creds = _config.navidrome_credentials
    if not creds:
        return None
    _server_url, username, password = creds
    salt = secrets.token_hex(8)
    token = hashlib.md5((password + salt).encode()).hexdigest()
    return {
        "u": username,
        "t": token,
        "s": salt,
        "c": "tv-automator",
        "v": "1.16.1",
        "f": "json",
    }


def _subsonic_stream_url(song_id: str) -> str | None:
    """Build a direct Navidrome stream URL for mpv to fetch."""
    params = _subsonic_params()
    if not params:
        return None
    params["id"] = song_id
    params.pop("f", None)
    server_url = _config.navidrome_credentials[0].rstrip("/")
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{server_url}/rest/stream?{qs}"


async def _navidrome_api(endpoint: str, extra_params: dict | None = None) -> dict:
    """Proxy a Subsonic API call and return the parsed subsonic-response."""
    params = _subsonic_params()
    if not params:
        raise HTTPException(503, "Navidrome not configured")
    if extra_params:
        params.update(extra_params)
    server_url = _config.navidrome_credentials[0].rstrip("/")
    url = f"{server_url}{endpoint}"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    resp = data.get("subsonic-response", {})
    if resp.get("status") != "ok":
        err = resp.get("error", {})
        raise HTTPException(502, err.get("message", "Navidrome error"))
    return resp


# ── mpv IPC helpers ────────────────────────────────────────────

async def _mpv_command(*args) -> dict | None:
    """Send a JSON IPC command to the running mpv instance."""
    try:
        reader, writer = await asyncio.open_unix_connection(_mpv_socket)
        cmd = json.dumps({"command": list(args)}) + "\n"
        writer.write(cmd.encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=2)
        writer.close()
        await writer.wait_closed()
        return json.loads(line) if line else None
    except Exception:
        return None


async def _mpv_get_property(prop: str):
    """Get a property from mpv."""
    resp = await _mpv_command("get_property", prop)
    if resp and "data" in resp:
        return resp["data"]
    return None


async def _mpv_set_property(prop: str, value):
    """Set a property on mpv."""
    return await _mpv_command("set_property", prop, value)


async def _mpv_start(url: str) -> None:
    """Start or restart mpv with a new audio URL."""
    global _mpv_proc
    await _mpv_stop()
    # Remove stale socket
    try:
        os.unlink(_mpv_socket)
    except FileNotFoundError:
        pass
    _mpv_proc = await asyncio.create_subprocess_exec(
        "mpv",
        "--no-video",
        "--no-terminal",
        f"--input-ipc-server={_mpv_socket}",
        "--idle=once",
        url,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    # Wait briefly for socket to appear
    for _ in range(20):
        if os.path.exists(_mpv_socket):
            break
        await asyncio.sleep(0.1)


async def _mpv_stop() -> None:
    """Stop the mpv process if running."""
    global _mpv_proc
    if _mpv_proc and _mpv_proc.returncode is None:
        try:
            _mpv_proc.terminate()
            await asyncio.wait_for(_mpv_proc.wait(), timeout=3)
        except Exception:
            try:
                _mpv_proc.kill()
            except Exception:
                pass
    _mpv_proc = None


def _music_build_shuffle_order() -> None:
    """Build a shuffled index order for the queue."""
    global _music_shuffle_order
    _music_shuffle_order = list(range(len(_music_queue)))
    random.shuffle(_music_shuffle_order)
    # Move current to front
    if _music_queue_index in _music_shuffle_order:
        _music_shuffle_order.remove(_music_queue_index)
        _music_shuffle_order.insert(0, _music_queue_index)


async def _music_play_current() -> None:
    """Play the song at the current queue index via mpv on the server."""
    if _music_queue_index < 0 or _music_queue_index >= len(_music_queue):
        return
    song = _music_queue[_music_queue_index]
    url = _subsonic_stream_url(song["id"])
    if not url:
        return
    await _mpv_start(url)


async def _music_advance(direction: int = 1) -> dict | None:
    """Advance the queue by direction (+1 next, -1 prev). Returns new song or None."""
    global _music_queue_index
    if not _music_queue:
        return None

    if _music_shuffle and _music_shuffle_order:
        cur_shuffle = _music_shuffle_order.index(_music_queue_index) if _music_queue_index in _music_shuffle_order else 0
        next_shuffle = cur_shuffle + direction
        if next_shuffle < 0 or next_shuffle >= len(_music_shuffle_order):
            if _music_repeat == "all":
                next_shuffle = next_shuffle % len(_music_shuffle_order)
            else:
                return None
        _music_queue_index = _music_shuffle_order[next_shuffle]
    else:
        _music_queue_index += direction
        if _music_queue_index < 0 or _music_queue_index >= len(_music_queue):
            if _music_repeat == "all":
                _music_queue_index = _music_queue_index % len(_music_queue)
            else:
                _music_queue_index = max(0, min(_music_queue_index, len(_music_queue) - 1))
                return None

    await _music_play_current()
    return _music_queue[_music_queue_index]


# ── mpv end-of-file watcher ───────────────────────────────────

_music_watcher_task: asyncio.Task | None = None


async def _music_watch_playback():
    """Watch mpv for track end and auto-advance the queue. Also broadcasts music status."""
    _tick = 0
    while True:
        await asyncio.sleep(1)
        _tick += 1

        # Check process state outside the lock — reads are safe without it.
        proc_done = not _mpv_proc or _mpv_proc.returncode is not None
        idle = False
        if not proc_done:
            idle = await _mpv_get_property("idle-active") or False

        if proc_done or idle:
            if not _music_queue:
                break
            async with _music_lock:
                # Re-check inside the lock: a command may have already advanced while
                # we were waiting to acquire it.
                if _mpv_proc and _mpv_proc.returncode is None:
                    still_idle = await _mpv_get_property("idle-active") or False
                    if not still_idle:
                        continue  # A command already started new playback
                if _music_repeat == "one":
                    await _music_play_current()
                    continue
                result = await _music_advance(1)
            if result is None:
                break  # End of queue
            await _broadcast_music_queue()
            continue

        # Broadcast music status every 2 seconds for position updates
        if _tick % 2 == 0 and _ws_clients:
            await _broadcast_music_status()


def _music_start_watcher():
    global _music_watcher_task
    if _music_watcher_task and not _music_watcher_task.done():
        _music_watcher_task.cancel()
    _music_watcher_task = asyncio.create_task(_music_watch_playback())


# ── Music API routes ──────────────────────────────────────────

@app.get("/api/music/ping")
async def music_ping():
    """Test Navidrome connection."""
    try:
        resp = await _navidrome_api("/rest/ping")
        return {"ok": True, "version": resp.get("version", "?")}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/music/credentials")
async def music_credentials(body: dict):
    """Save Navidrome credentials and verify connection."""
    server_url = body.get("server_url", "").strip().rstrip("/")
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    if not server_url or not username or not password:
        raise HTTPException(400, "All fields are required")
    _config.update_nested("navidrome", "server_url", value=server_url)
    _config.update_nested("navidrome", "username", value=username)
    _config.update_nested("navidrome", "password", value=password)
    _config.save_user_config()
    try:
        resp = await _navidrome_api("/rest/ping")
        log.info("Navidrome connected: %s@%s", username, server_url)
        return {"success": True, "version": resp.get("version", "?")}
    except Exception as e:
        log.warning("Navidrome credentials saved but ping failed: %s", e)
        return {"success": False, "error": str(e)}


@app.get("/api/music/artists")
async def music_artists():
    resp = await _navidrome_api("/rest/getArtists")
    return resp.get("artists", {})


@app.get("/api/music/albums")
async def music_albums(type: str = "recent", size: int = 40, offset: int = 0):
    resp = await _navidrome_api("/rest/getAlbumList2", {
        "type": type, "size": str(size), "offset": str(offset),
    })
    return resp.get("albumList2", {})


@app.get("/api/music/album/{album_id}")
async def music_album(album_id: str):
    resp = await _navidrome_api("/rest/getAlbum", {"id": album_id})
    return resp.get("album", {})

@app.get("/api/music/artist/{artist_id}")
async def music_artist(artist_id: str):
    resp = await _navidrome_api("/rest/getArtist", {"id": artist_id})
    return resp.get("artist", {})


@app.get("/api/music/search")
async def music_search(query: str = "", artistCount: int = 5, albumCount: int = 10, songCount: int = 20):
    resp = await _navidrome_api("/rest/search3", {
        "query": query,
        "artistCount": str(artistCount),
        "albumCount": str(albumCount),
        "songCount": str(songCount),
    })
    return resp.get("searchResult3", {})


@app.get("/api/music/playlists")
async def music_playlists():
    resp = await _navidrome_api("/rest/getPlaylists")
    return resp.get("playlists", {})


@app.get("/api/music/playlist/{playlist_id}")
async def music_playlist(playlist_id: str):
    resp = await _navidrome_api("/rest/getPlaylist", {"id": playlist_id})
    return resp.get("playlist", {})


@app.get("/api/music/radio")
async def music_radio():
    resp = await _navidrome_api("/rest/getInternetRadioStations")
    return resp.get("internetRadioStations", {})


@app.get("/api/music/cover/{item_id}")
async def music_cover(item_id: str, size: int = 300):
    """Proxy album art from Navidrome with browser caching."""
    params = _subsonic_params()
    if not params:
        raise HTTPException(503, "Navidrome not configured")
    params["id"] = item_id
    params["size"] = str(size)
    params.pop("f", None)
    server_url = _config.navidrome_credentials[0].rstrip("/")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{server_url}/rest/getCoverArt", params=params)
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Cover art not found")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── Server-side playback control ──────────────────────────────

@app.post("/api/music/play")
async def music_play(body: dict):
    """Start playing a queue of songs on the server. Body: {songs: [...], index: 0}."""
    global _music_queue, _music_queue_index, _music_shuffle_order
    songs = body.get("songs", [])
    index = body.get("index", 0)
    if not songs:
        raise HTTPException(400, "No songs provided")
    async with _play_lock:
        await _stop_video_for_music()
    async with _music_lock:
        _music_queue[:] = [
            {"id": s["id"], "title": s.get("title", ""), "artist": s.get("artist", ""),
             "albumId": s.get("albumId") or s.get("parent", ""), "duration": s.get("duration", 0)}
            for s in songs
        ]
        _music_queue_index = index
        if _music_shuffle:
            _music_build_shuffle_order()
        await _music_play_current()
        _music_start_watcher()
    await _broadcast_music_status()
    await _broadcast_music_queue()
    return {"playing": True, "song": _music_queue[_music_queue_index]}


@app.post("/api/music/command")
async def music_command(body: dict):
    """Send a transport command. Body: {command: pause|resume|next|prev|stop|seek, value: ...}."""
    global _music_queue_index, _music_shuffle, _music_repeat, _music_shuffle_order
    cmd = body.get("command", "")
    result: dict = {"ok": True}
    queue_changed = False

    if cmd == "pause":
        await _mpv_set_property("pause", True)
    elif cmd == "resume":
        await _mpv_set_property("pause", False)
    elif cmd == "toggle":
        paused = await _mpv_get_property("pause")
        await _mpv_set_property("pause", not paused)
        result["paused"] = not paused
    elif cmd == "next":
        async with _music_lock:
            result["song"] = await _music_advance(1)
            _music_start_watcher()
        queue_changed = True
    elif cmd == "prev":
        pos = await _mpv_get_property("time-pos")
        if pos and pos > 3:
            await _mpv_command("seek", 0, "absolute")
        else:
            async with _music_lock:
                result["song"] = await _music_advance(-1)
                _music_start_watcher()
            queue_changed = True
    elif cmd == "stop":
        await _stop_music_internal()
        queue_changed = True
    elif cmd == "seek":
        val = body.get("value", 0)
        await _mpv_command("seek", val, "absolute")
    elif cmd == "volume":
        val = max(0, min(100, float(body.get("value", 100))))
        await _mpv_set_property("volume", val)
    elif cmd == "jump":
        idx = int(body.get("value", 0))
        async with _music_lock:
            if 0 <= idx < len(_music_queue):
                _music_queue_index = idx
                await _music_play_current()
                _music_start_watcher()
                queue_changed = True
            else:
                raise HTTPException(400, "Invalid queue index")
    elif cmd == "shuffle":
        async with _music_lock:
            _music_shuffle = body.get("value", not _music_shuffle)
            if _music_shuffle:
                _music_build_shuffle_order()
            else:
                _music_shuffle_order.clear()
            result["shuffle"] = _music_shuffle
    elif cmd == "repeat":
        async with _music_lock:
            modes = ["off", "all", "one"]
            idx = modes.index(_music_repeat) if _music_repeat in modes else 0
            _music_repeat = modes[(idx + 1) % 3]
            result["repeat"] = _music_repeat
    else:
        raise HTTPException(400, f"Unknown command: {cmd}")

    # Broadcast changes to all clients
    await _broadcast_music_status()
    if queue_changed:
        await _broadcast_music_queue()
    return result


@app.get("/api/music/status")
async def music_status():
    """Get current music playback state from the server-side mpv player."""
    playing = _mpv_proc is not None and _mpv_proc.returncode is None
    song = _music_queue[_music_queue_index] if 0 <= _music_queue_index < len(_music_queue) else None
    result = {
        "playing": playing,
        "song": song,
        "queue_length": len(_music_queue),
        "queue_index": _music_queue_index,
        "shuffle": _music_shuffle,
        "repeat": _music_repeat,
        "position": 0,
        "duration": 0,
        "paused": False,
    }
    if playing:
        result["position"] = await _mpv_get_property("time-pos") or 0
        result["duration"] = await _mpv_get_property("duration") or 0
        result["paused"] = await _mpv_get_property("pause") or False
        result["volume"] = await _mpv_get_property("volume") or 100
    return result


@app.get("/api/music/sinks")
async def music_sinks():
    """List available PulseAudio output sinks on the server."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "list", "sinks", "short",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        sinks = []
        for line in stdout.decode().strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                sinks.append({"id": parts[0], "name": parts[1], "state": parts[4] if len(parts) > 4 else ""})
        # Also get the default sink
        proc2 = await asyncio.create_subprocess_exec(
            "pactl", "get-default-sink",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await proc2.communicate()
        default_sink = stdout2.decode().strip()
        return {"sinks": sinks, "default": default_sink}
    except Exception as e:
        log.exception("Failed to list sinks")
        return {"sinks": [], "default": "", "error": str(e)}


@app.post("/api/music/sink")
async def music_set_sink(body: dict):
    """Set the default PulseAudio output sink on the server."""
    sink_name = body.get("sink", "")
    if not sink_name:
        raise HTTPException(400, "Sink name required")
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "set-default-sink", sink_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(500, f"Failed to set sink: {stderr.decode()}")
        # Also move the running mpv stream to the new sink
        if _mpv_proc and _mpv_proc.returncode is None:
            # Find mpv's sink input and move it
            proc2 = await asyncio.create_subprocess_exec(
                "pactl", "list", "sink-inputs", "short",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await proc2.communicate()
            for line in stdout2.decode().strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 1:
                    input_id = parts[0]
                    await asyncio.create_subprocess_exec(
                        "pactl", "move-sink-input", input_id, sink_name,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    )
        return {"ok": True, "sink": sink_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/music/queue")
async def music_get_queue():
    """Return the current playback queue and active index."""
    return {"songs": _music_queue, "index": _music_queue_index}


@app.post("/api/music/queue/append")
async def music_queue_append(body: dict):
    """Append songs to the queue without stopping current playback."""
    global _music_queue_index, _music_shuffle_order
    songs = body.get("songs", [])
    if not songs:
        raise HTTPException(400, "No songs provided")
    need_start = False
    async with _music_lock:
        for s in songs:
            _music_queue.append({
                "id": s["id"], "title": s.get("title", ""), "artist": s.get("artist", ""),
                "albumId": s.get("albumId") or s.get("parent", ""), "duration": s.get("duration", 0),
            })
        if _music_shuffle:
            _music_build_shuffle_order()
        if _music_queue_index < 0:
            need_start = True
            _music_queue_index = 0
    if need_start:
        async with _play_lock:
            await _stop_video_for_music()
        async with _music_lock:
            await _music_play_current()
            _music_start_watcher()
    await _broadcast_music_queue()
    await _broadcast_music_status()
    return {"ok": True, "queue_length": len(_music_queue)}


@app.post("/api/music/queue/remove")
async def music_queue_remove(body: dict):
    """Remove a song from the queue by index."""
    global _music_queue_index
    idx = body.get("index")
    if idx is None or idx < 0 or idx >= len(_music_queue):
        raise HTTPException(400, "Invalid index")
    async with _music_lock:
        _music_queue.pop(idx)
        if _music_shuffle:
            _music_build_shuffle_order()
        if idx < _music_queue_index:
            _music_queue_index -= 1
        elif idx == _music_queue_index:
            if _music_queue:
                _music_queue_index = min(_music_queue_index, len(_music_queue) - 1)
                await _music_play_current()
                _music_start_watcher()
            else:
                _music_queue_index = -1
                await _mpv_stop()
    await _broadcast_music_queue()
    await _broadcast_music_status()
    return {"ok": True, "queue_length": len(_music_queue)}


@app.get("/api/music/starred")
async def music_starred():
    """Get starred (favourited) songs, albums, and artists."""
    resp = await _navidrome_api("/rest/getStarred2")
    return resp.get("starred2", {})


@app.post("/api/music/star")
async def music_star(body: dict):
    """Star or unstar a song/album/artist."""
    item_id = body.get("id")
    action = body.get("action", "star")  # "star" or "unstar"
    if not item_id:
        raise HTTPException(400, "ID required")
    endpoint = "/rest/star" if action == "star" else "/rest/unstar"
    await _navidrome_api(endpoint, {"id": item_id})
    return {"ok": True}


# ── Health ──────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    ok = _browser.is_healthy and _session.is_authenticated
    return {
        "healthy": ok,
        "browser": _browser.is_healthy,
        "authenticated": _session.is_authenticated,
        "now_playing": _now_playing_game_id,
        "heartbeat": _heartbeat_task is not None and not _heartbeat_task.done(),
    }


# ── WebSocket ───────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        # Send initial state
        games = _scheduler.get_games_for_provider("mlb")
        await websocket.send_json({
            "type": "games",
            "games": [_game_to_dict(g) for g in games],
        })
        await websocket.send_json({
            "type": "status",
            "now_playing_game_id": _now_playing_game_id,
            "youtube_mode": _youtube_mode,
            "youtube_video_id": _youtube_video_id,
            "authenticated": _session.is_authenticated,
            "browser_running": _browser.is_running,
            "heartbeat_active": _heartbeat_task is not None and not _heartbeat_task.done(),
        })
        if _autoplay_queue:
            await websocket.send_json({"type": "autoplay", "queued": True, **_autoplay_queue})
        else:
            await websocket.send_json({"type": "autoplay", "queued": False, "game_id": None})
        # Settings
        settings_data = await get_settings()
        await websocket.send_json({"type": "settings", **settings_data})
        # Music status
        music_data = await music_status()
        await websocket.send_json({"type": "music", **music_data})
        # Volume
        try:
            vol_data = await get_volume()
            await websocket.send_json({"type": "volume", **vol_data})
        except Exception:
            pass
        # Music queue
        await websocket.send_json({"type": "queue", "songs": _music_queue, "index": _music_queue_index})
        # Keep alive — wait for client disconnect
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


# ── Pages ───────────────────────────────────────────────────────

@app.get("/player", response_class=HTMLResponse)
async def player_page():
    return _PLAYER_HTML


@app.get("/screensaver", response_class=HTMLResponse)
async def screensaver_page():
    return _SCREENSAVER_HTML


@app.get("/tv/youtube", response_class=HTMLResponse)
async def youtube_page():
    return _YOUTUBE_HTML



_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/static/{filename}")
async def static_file(filename: str):
    path = _STATIC_DIR / filename
    if not path.is_file():
        raise HTTPException(404)
    media = "application/javascript" if filename.endswith(".js") else "application/octet-stream"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/{filename}", include_in_schema=False)
async def serve_root_asset(filename: str):
    """Serve root-level public assets from the React dist directory (e.g. favicon.svg)."""
    safe_name = os.path.basename(filename)
    if not safe_name:
        raise HTTPException(status_code=404)
    file_path = _FRONTEND_DIST / safe_name
    if file_path.is_file():
        return FileResponse(str(file_path))
    raise HTTPException(status_code=404)
