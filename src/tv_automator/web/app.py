"""FastAPI web dashboard for TV-Automator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from tv_automator.automator.browser_control import BrowserController
from tv_automator.automator.cec_control import CECController
from tv_automator.settings import AppSettings
from tv_automator.providers.base import Game, GameStatus, StreamingProvider
from tv_automator.providers.mlb import MLBProvider, MLB_TEAMS
from tv_automator.providers.mlb_session import MLBSession
from tv_automator.scheduler.game_scheduler import GameScheduler

from tv_automator.web import music, player, youtube

log = logging.getLogger(__name__)

# ── Templates ───────────────────────────────────────────────────
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_PLAYER_HTML = (_TEMPLATE_DIR / "player.html").read_text()
_SCREENSAVER_HTML = (_TEMPLATE_DIR / "screensaver.html").read_text()
_YOUTUBE_HTML = (_TEMPLATE_DIR / "youtube.html").read_text()

_PACIFIC = ZoneInfo("America/Los_Angeles")

# ── App state ────────────────────────────────────────────────────

_settings: AppSettings
_browser: BrowserController
_cec: CECController
_mlb: MLBProvider
_session: MLBSession
_scheduler: GameScheduler

_play_lock: asyncio.Lock
_watchdog_task: asyncio.Task | None = None
CHROME_RECYCLE_HOURS = 8

_autoplay_queue: dict | None = None
_ws_clients: set[WebSocket] = set()
_last_games_hash: str = ""

_last_batter_id: int | None = None
_batter_vs_pitcher_cache: dict[tuple[int, int], dict | None] = {}
_other_scores_cache: list[dict] = []
_other_scores_cache_time: float = 0
OTHER_SCORES_TTL = 30

_http_client: httpx.AsyncClient | None = None


def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "/data"))


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=10))
    return _http_client


@dataclass
class AppContext:
    browser: BrowserController
    session: MLBSession
    cec: CECController
    settings: AppSettings
    scheduler: GameScheduler
    play_lock: asyncio.Lock
    ws_clients: set[WebSocket]
    broadcast: Callable[[dict], Awaitable[None]]
    broadcast_status: Callable[[], Awaitable[None]]
    do_stop: Callable[[], Awaitable[None]]
    stop_video_for_music: Callable[[], Awaitable[None]]


# ── Broadcast helpers ─────────────────────────────────────────────

async def _broadcast(message: dict) -> None:
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
    await _broadcast({
        "type": "status",
        "now_playing_game_id": player.get_now_playing_game_id(),
        "youtube_mode": youtube.get_youtube_mode(),
        "youtube_video_id": youtube.get_youtube_video_id(),
        "authenticated": _session.is_authenticated,
        "browser_running": _browser.is_running,
        "heartbeat_active": player.heartbeat_active(),
    })


async def _broadcast_autoplay_state() -> None:
    if _autoplay_queue:
        await _broadcast({"type": "autoplay", "queued": True, **_autoplay_queue})
    else:
        await _broadcast({"type": "autoplay", "queued": False, "game_id": None})


async def _broadcast_settings() -> None:
    data = await get_settings()
    await _broadcast({"type": "settings", **data})


async def _broadcast_volume() -> None:
    try:
        data = await get_volume()
        await _broadcast({"type": "volume", **data})
    except Exception:
        pass


# ── Stop helpers ──────────────────────────────────────────────────

async def _do_stop() -> None:
    player.stop_heartbeat()
    player.stop_expiry_timer()
    youtube.stop_progress_task()
    if youtube.get_youtube_mode():
        await youtube.save_current_progress()

    player.clear_player_state()
    youtube.clear_youtube_state()

    if _browser.is_running:
        await _browser.navigate("http://127.0.0.1:5000/screensaver")

    if _cec.enabled and _settings.get("cec_power_off_on_stop", True):
        await _cec.power_off()

    await _broadcast_status()


async def _stop_video_for_music() -> None:
    """Stop active video playback (game or YouTube) so music can take over.
    Called within _play_lock via ctx.stop_video_for_music()"""
    was_playing = bool(player.get_now_playing_game_id() or youtube.get_youtube_mode())

    player.stop_heartbeat()
    player.stop_expiry_timer()
    youtube.stop_progress_task()
    if youtube.get_youtube_mode() and youtube.get_youtube_video_id():
        await youtube.save_current_progress()

    player.clear_player_state()
    youtube.clear_youtube_state()

    if was_playing and _browser.is_running:
        await _browser.navigate("http://127.0.0.1:5000/screensaver")

    if was_playing:
        await _broadcast_status()


# ── Lifespan & Background tasks ───────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _settings, _browser, _cec, _mlb, _session, _scheduler, _play_lock, _watchdog_task, _http_client
    _settings = AppSettings(_data_dir())
    _browser = BrowserController()
    _cec = CECController(enabled=_settings.get("cec_enabled", False))
    _mlb = MLBProvider()
    _session = MLBSession()
    _scheduler = GameScheduler(_settings)
    _scheduler.register_provider(_mlb)
    _scheduler.set_on_refresh(_on_schedule_refresh)
    _play_lock = asyncio.Lock()

    ctx = AppContext(
        browser=_browser,
        session=_session,
        cec=_cec,
        settings=_settings,
        scheduler=_scheduler,
        play_lock=_play_lock,
        ws_clients=_ws_clients,
        broadcast=_broadcast,
        broadcast_status=_broadcast_status,
        do_stop=_do_stop,
        stop_video_for_music=_stop_video_for_music,
    )

    music.init(ctx)
    youtube.init(ctx)
    player.init(ctx)

    try:
        await _browser.start()
        player.set_browser_started_at(time.monotonic())
        log.info("Browser started")
    except Exception:
        log.exception("Browser failed to start — check DISPLAY / X11")

    creds = _settings.mlb_credentials
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

    _scheduler.set_auto_start_callback(_auto_start_game)
    await _scheduler.start()
    _watchdog_task = asyncio.create_task(_watchdog_loop())

    _http_client = httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=10))

    if _browser.is_running:
        asyncio.create_task(_initial_navigate())

    yield

    if _watchdog_task:
        _watchdog_task.cancel()
    player.stop_heartbeat()
    player.stop_expiry_timer()
    youtube.stop_progress_task()
    await _scheduler.stop()
    await _session.close()
    await _browser.stop()
    if _http_client:
        await _http_client.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(music.router)
app.include_router(youtube.router)
app.include_router(player.router)


async def _initial_navigate() -> None:
    for _ in range(20):
        await asyncio.sleep(0.5)
        if await _browser.navigate("http://127.0.0.1:5000/screensaver"):
            return
    log.warning("Initial screensaver navigation failed after retries")


async def _watchdog_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            if not _browser.is_healthy:
                log.warning("Watchdog: browser unhealthy — restarting...")
                if await _browser.restart():
                    player.set_browser_started_at(time.monotonic())
                    if player.get_now_playing_game_id():
                        log.info("Watchdog: reconnecting stream after browser restart...")
                        await player.do_reconnect()
                    else:
                        await _browser.navigate("http://127.0.0.1:5000/screensaver")

            elif (
                _browser.is_running
                and not player.get_now_playing_game_id()
                and player.get_browser_started_at()
                and (time.monotonic() - player.get_browser_started_at()) > CHROME_RECYCLE_HOURS * 3600
            ):
                log.info("Watchdog: recycling Chrome after %dh idle", CHROME_RECYCLE_HOURS)
                if await _browser.restart():
                    player.set_browser_started_at(time.monotonic())
                    await _browser.navigate("http://127.0.0.1:5000/screensaver")

            if _session._username and not _session.is_authenticated:
                log.info("Watchdog: token expiring — refreshing...")
                await _session.ensure_authenticated()

        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Watchdog error")


# ── Scheduler callbacks ───────────────────────────────────────────

async def _auto_start_game(provider: StreamingProvider, game: Game) -> None:
    async with _play_lock:
        if player.get_now_playing_game_id():
            log.info("Auto-start skipped — already playing %s", player.get_now_playing_game_id())
            return

        fav_teams = {t.upper() for t in _settings.favorite_teams}
        if game.home_team.abbreviation.upper() in fav_teams:
            feed = "HOME"
        elif game.away_team.abbreviation.upper() in fav_teams:
            feed = "AWAY"
        else:
            feed = "HOME"

        log.info("Auto-starting: %s (feed=%s)", game.display_matchup, feed)
        try:
            await player.do_play(game.game_id, feed)
        except Exception:
            log.exception("Auto-start failed for %s", game.game_id)


async def _auto_start_queued(queue_entry: dict) -> None:
    async with _play_lock:
        if player.get_now_playing_game_id():
            log.info("Queued auto-start skipped — already playing %s", player.get_now_playing_game_id())
            return
        try:
            await player.do_play(queue_entry["game_id"], queue_entry.get("feed", "HOME"))
        except Exception:
            log.exception("Queued auto-start failed for %s", queue_entry["game_id"])


async def _on_schedule_refresh() -> None:
    global _last_games_hash, _autoplay_queue
    games = _scheduler.get_games_for_provider("mlb")

    if _autoplay_queue and not player.get_now_playing_game_id():
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


# ── Frontend Routes ───────────────────────────────────────────────

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


# ── MLB & Game Data Routes ────────────────────────────────────────

@app.get("/api/games")
async def get_games(date: str | None = None):
    now_pacific = datetime.now(_PACIFIC)
    target = datetime.fromisoformat(date) if date else now_pacific
    if target.date() == now_pacific.date():
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
        await music.stop_music_internal()
        player.stop_heartbeat()
        if feed.upper() == "CONDENSED":
            info = await player.do_play_condensed(game_id)
        else:
            info = await player.do_play(game_id, feed.upper())
        return {"success": True, "feed": feed.upper()}


@app.post("/api/stop")
async def stop_playback():
    async with _play_lock:
        await _do_stop()
    return {"success": True}


@app.post("/api/video-ended")
async def video_ended():
    async with _play_lock:
        await _do_stop()
    log.info("Video ended — returned to screensaver")
    return {"success": True}


async def _fetch_other_scores() -> list[dict]:
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
                if gid == player.get_now_playing_game_id():
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


def _get_pitcher_summary(boxscore: dict, linescore: dict, inning_state: str) -> dict | None:
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


@app.get("/api/pitches")
async def get_pitches(game_id: str | None = None):
    global _last_batter_id

    empty = {"pitches": [], "batter": "", "pitcher": "", "count": "", "outs": 0,
             "inning": "", "batter_intel": None, "break_data": None}

    target_id = game_id or player.get_now_playing_game_id()
    if not target_id:
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
        inning_state = linescore.get("inningState", "")

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

        zone_top = 3.4
        zone_bot = 1.6
        if pitches:
            zone_top = pitches[-1].get("zone_top", 3.4)
            zone_bot = pitches[-1].get("zone_bot", 1.6)

        batter_intel = None
        if batter_id:
            is_new = batter_id != _last_batter_id
            _last_batter_id = batter_id

            bat_team = "away" if inning_half == "Top" else "home"
            bp = boxscore.get("teams", {}).get(bat_team, {}).get("players", {}).get(f"ID{batter_id}", {})
            season = bp.get("seasonStats", {}).get("batting", {})
            today = bp.get("stats", {}).get("batting", {})

            vs = None
            cache_key = (batter_id, pitcher_id) if pitcher_id else None
            if cache_key and cache_key in _batter_vs_pitcher_cache:
                vs = _batter_vs_pitcher_cache[cache_key]
            elif cache_key:
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

        break_data = None
        if inning_state in ("Middle", "End"):
            other_scores = await _fetch_other_scores()
            due_up = _get_due_up(boxscore, inning_state)
            pitcher_summary = _get_pitcher_summary(boxscore, linescore, inning_state)
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

        offense = linescore.get("offense", {})
        runners = {
            "first": "first" in offense,
            "second": "second" in offense,
            "third": "third" in offense
        }

        ls_teams = linescore.get("teams", {})
        gd_teams = data.get("gameData", {}).get("teams", {})
        score = {
            "away": ls_teams.get("away", {}).get("runs", 0),
            "home": ls_teams.get("home", {}).get("runs", 0),
            "away_abbr": gd_teams.get("away", {}).get("abbreviation", ""),
            "home_abbr": gd_teams.get("home", {}).get("abbreviation", ""),
        }

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

    all_plays = plays.get("allPlays", [])
    win_prob = []
    for p in all_plays:
        hwp = p.get("contextMetrics", {}).get("homeWinProbability")
        ab = p.get("about", {}).get("atBatIndex")
        if hwp is not None and ab is not None:
            win_prob.append({"ab": ab, "hwp": round(hwp, 1)})

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


# ── System / Settings Routes ──────────────────────────────────────

@app.get("/api/status")
async def get_status():
    return {
        "now_playing_game_id": player.get_now_playing_game_id(),
        "now_playing_feed": player.get_now_playing_feed(),
        "youtube_mode": youtube.get_youtube_mode(),
        "authenticated": _session.is_authenticated,
        "browser_running": _browser.is_running,
        "heartbeat_active": player.heartbeat_active(),
    }


@app.get("/api/health")
async def health_check():
    ok = _browser.is_healthy and _session.is_authenticated
    return {
        "healthy": ok,
        "browser": _browser.is_healthy,
        "authenticated": _session.is_authenticated,
        "now_playing": player.get_now_playing_game_id(),
        "heartbeat": player.heartbeat_active(),
    }


@app.get("/api/volume")
async def get_volume():
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


@app.get("/api/teams")
async def get_teams():
    return MLB_TEAMS


@app.get("/api/favorites")
async def get_favorites():
    return {"teams": _settings.favorite_teams}


@app.post("/api/favorites")
async def set_favorites(body: dict):
    teams = body.get("teams", [])
    _settings.set("favorite_teams", teams)
    _settings.save()
    log.info("Favorites updated: %s", teams)
    return {"teams": teams}


@app.get("/api/settings")
async def get_settings():
    return {
        **_settings.public_dict(),
        "mlb_authenticated": _session.is_authenticated,
        "navidrome_server_url": os.getenv("NAVIDROME_URL") or _settings.get("navidrome_server_url", ""),
        "navidrome_username": os.getenv("NAVIDROME_USERNAME") or _settings.get("navidrome_username", ""),
        "mlb_username": os.getenv("MLB_USERNAME") or _settings.get("mlb_username", ""),
    }


@app.post("/api/settings")
async def update_settings(body: dict):
    patch: dict = {}
    if "auto_start" in body:
        patch["auto_start"] = bool(body["auto_start"])
    if "default_feed" in body:
        patch["default_feed"] = body["default_feed"].upper() if body["default_feed"] in ("HOME", "AWAY") else "HOME"
    if "strike_zone_enabled" in body:
        patch["strike_zone_enabled"] = bool(body["strike_zone_enabled"])
    if "strike_zone_size" in body:
        patch["strike_zone_size"] = body["strike_zone_size"] if body["strike_zone_size"] in ("small", "medium", "large") else "medium"
    if "batter_intel_enabled" in body:
        patch["batter_intel_enabled"] = bool(body["batter_intel_enabled"])
    if "between_innings_enabled" in body:
        patch["between_innings_enabled"] = bool(body["between_innings_enabled"])
    if "overlay_delay" in body:
        patch["overlay_delay"] = max(0, min(15, float(body["overlay_delay"])))
    if "poll_interval" in body:
        patch["poll_interval"] = max(15, min(300, int(body["poll_interval"])))
    if "pre_game_minutes" in body:
        patch["pre_game_minutes"] = max(0, min(30, int(body["pre_game_minutes"])))
    if "cec_enabled" in body:
        patch["cec_enabled"] = bool(body["cec_enabled"])
        _cec._enabled = body["cec_enabled"]
    if "cec_power_off_on_stop" in body:
        patch["cec_power_off_on_stop"] = bool(body["cec_power_off_on_stop"])
    if "suggested_channels" in body:
        patch["suggested_channels"] = body["suggested_channels"]
        youtube._suggested_cache_time = 0
    if "screensaver_music_size" in body:
        patch["screensaver_music_size"] = body["screensaver_music_size"] if body["screensaver_music_size"] in ("small", "medium", "large") else "medium"
    if "screensaver_schedule_scale" in body:
        patch["screensaver_schedule_scale"] = max(50, min(200, int(body["screensaver_schedule_scale"])))

    _settings.update(patch)
    _settings.save()
    log.info("Settings updated: %s", list(patch.keys()))
    await _broadcast_settings()
    return await get_settings()


@app.post("/api/settings/credentials")
async def update_credentials(body: dict):
    username = body.get("mlb_username", "").strip()
    password = body.get("mlb_password", "").strip()
    if not username or not password:
        raise HTTPException(400, "Username and password are required")

    ok = await _session.login(username, password)
    if ok:
        _settings.update({"mlb_username": username, "mlb_password": password})
        _settings.save()
        log.info("Credentials updated and login successful for %s", username)
        asyncio.create_task(_scheduler.refresh())
        await _broadcast_status()
        return {"success": True, "authenticated": True}

    log.warning("Login failed for %s — credentials not saved", username)
    await _broadcast_status()
    return {"success": False, "authenticated": False, "error": "Login failed — check username/password"}


@app.get("/api/autoplay")
async def get_autoplay():
    if not _autoplay_queue:
        return {"queued": False, "game_id": None, "feed": None}
    return {"queued": True, **_autoplay_queue}


@app.post("/api/autoplay")
async def set_autoplay(body: dict):
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


# ── WebSockets ────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        games = _scheduler.get_games_for_provider("mlb")
        await websocket.send_json({
            "type": "games",
            "games": [_game_to_dict(g) for g in games],
        })
        await websocket.send_json({
            "type": "status",
            "now_playing_game_id": player.get_now_playing_game_id(),
            "youtube_mode": youtube.get_youtube_mode(),
            "youtube_video_id": youtube.get_youtube_video_id(),
            "authenticated": _session.is_authenticated,
            "browser_running": _browser.is_running,
            "heartbeat_active": player.heartbeat_active(),
        })
        if _autoplay_queue:
            await websocket.send_json({"type": "autoplay", "queued": True, **_autoplay_queue})
        else:
            await websocket.send_json({"type": "autoplay", "queued": False, "game_id": None})

        settings_data = await get_settings()
        await websocket.send_json({"type": "settings", **settings_data})

        music_data = await music.get_status()
        await websocket.send_json({"type": "music", **music_data})

        try:
            vol_data = await get_volume()
            await websocket.send_json({"type": "volume", **vol_data})
        except Exception:
            pass

        music_q = music.get_queue_state()
        await websocket.send_json({"type": "queue", **music_q})

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


# ── Static Pages ──────────────────────────────────────────────────

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
    safe_name = os.path.basename(filename)
    if not safe_name:
        raise HTTPException(status_code=404)
    file_path = _FRONTEND_DIST / safe_name
    if file_path.is_file():
        return FileResponse(str(file_path))
    raise HTTPException(status_code=404)
