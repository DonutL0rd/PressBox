"""FastAPI web dashboard for TV-Automator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from urllib.parse import urljoin

import httpx

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, Response

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
_DASHBOARD_HTML = (_TEMPLATE_DIR / "dashboard.html").read_text()
_SCREENSAVER_HTML = (_TEMPLATE_DIR / "screensaver.html").read_text()

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
_autoplay_queue: dict | None = None  # {game_id, feed, display_matchup, display_time}
_play_lock: asyncio.Lock
_heartbeat_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None
_expiry_task: asyncio.Task | None = None
_browser_started_at: float = 0  # monotonic time
CHROME_RECYCLE_HOURS = 8  # restart Chrome after this many hours of idle

# WebSocket clients
_ws_clients: set[WebSocket] = set()
_last_games_hash: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _browser, _cec, _mlb, _session, _scheduler, _play_lock, _watchdog_task
    _config = Config()
    _browser = BrowserController(_config)
    _cec = CECController(enabled=_config.cec.get("enabled", False))
    _mlb = MLBProvider()
    _session = MLBSession()
    _scheduler = GameScheduler(_config)
    _scheduler.register_provider(_mlb)
    _scheduler.set_on_refresh(_on_schedule_refresh)
    _play_lock = asyncio.Lock()

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

    # Navigate to screensaver on startup
    if _browser.is_running:
        await _browser.navigate("http://127.0.0.1:5000/screensaver")

    yield

    # Shutdown
    if _watchdog_task:
        _watchdog_task.cancel()
    _stop_heartbeat()
    _stop_expiry_timer()
    await _scheduler.stop()
    await _session.close()
    await _browser.stop()


app = FastAPI(lifespan=lifespan)


# ── Background tasks ────────────────────────────────────────────

async def _heartbeat_loop() -> None:
    """Send periodic heartbeats to keep the MLB stream alive."""
    while True:
        if not _stream_info or not _stream_info.heartbeat_url:
            return
        await asyncio.sleep(_stream_info.heartbeat_interval)
        ok = await _session.send_heartbeat(_stream_info.heartbeat_url)
        if ok:
            log.debug("Heartbeat OK")
        else:
            log.warning("Heartbeat failed — stream may drop soon")


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


async def _do_reconnect() -> StreamInfo | None:
    """Get a fresh stream URL for the current game and reload the player."""
    global _stream_info

    if not _now_playing_game_id:
        return None

    log.info("Reconnecting stream for game %s (feed=%s)...",
             _now_playing_game_id, _now_playing_feed)

    _stop_heartbeat()
    _stop_expiry_timer()

    try:
        info = await _session.get_stream_info(_now_playing_game_id, _now_playing_feed)
        if not info:
            log.error("Reconnect failed — no stream URL")
            return None

        _stream_info = info
        _start_heartbeat()
        _start_expiry_timer()

        await _browser.navigate("http://127.0.0.1:5000/player")
        log.info("Reconnected successfully")
        return info
    except Exception:
        log.exception("Reconnect failed")
        return None


async def _do_stop() -> None:
    global _now_playing_game_id, _now_playing_feed, _stream_info
    _stop_heartbeat()
    _stop_expiry_timer()
    _now_playing_game_id = None
    _now_playing_feed = "HOME"
    _stream_info = None

    # Navigate to screensaver instead of blank
    if _browser.is_running:
        await _browser.navigate("http://127.0.0.1:5000/screensaver")

    # CEC: power off TV
    if _cec.enabled and _config.cec.get("power_off_on_stop", True):
        await _cec.power_off()

    await _broadcast_status()


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

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return _DASHBOARD_HTML


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
        _stop_heartbeat()
        info = await _do_play(game_id, feed.upper())
        return {"success": True, "feed": feed.upper()}


@app.post("/api/stop")
async def stop_playback():
    async with _play_lock:
        await _do_stop()
    return {"success": True}


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
        "authenticated": _session.is_authenticated,
        "browser_running": _browser.is_running,
        "heartbeat_active": _heartbeat_task is not None and not _heartbeat_task.done(),
    }


@app.get("/api/stream")
async def get_stream():
    if not _stream_info:
        raise HTTPException(404, "No stream active")
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
        return await get_volume()
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
    return {
        "auto_start": _config.auto_start,
        "cec_enabled": _config.cec.get("enabled", False),
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
    if "auto_start" in body:
        _config.update_nested("providers", "mlb", "auto_start", value=body["auto_start"])
    if "cec_enabled" in body:
        _config.update_nested("cec", "enabled", value=body["cec_enabled"])
        _cec._enabled = body["cec_enabled"]
    _config.save_user_config()
    log.info("Settings updated: %s", body)
    return await get_settings()


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
            "authenticated": _session.is_authenticated,
            "browser_running": _browser.is_running,
            "heartbeat_active": _heartbeat_task is not None and not _heartbeat_task.done(),
        })
        if _autoplay_queue:
            await websocket.send_json({"type": "autoplay", "queued": True, **_autoplay_queue})
        else:
            await websocket.send_json({"type": "autoplay", "queued": False, "game_id": None})
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


_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/static/{filename}")
async def static_file(filename: str):
    path = _STATIC_DIR / filename
    if not path.is_file():
        raise HTTPException(404)
    media = "application/javascript" if filename.endswith(".js") else "application/octet-stream"
    return FileResponse(path, media_type=media)
