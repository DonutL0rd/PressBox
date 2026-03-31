"""FastAPI web dashboard for TV-Automator."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse

from tv_automator.automator.browser_control import BrowserController
from tv_automator.config import Config
from tv_automator.providers.base import Game
from tv_automator.providers.mlb import MLBProvider
from tv_automator.providers.mlb_session import MLBSession, StreamInfo
from tv_automator.scheduler.game_scheduler import GameScheduler

log = logging.getLogger(__name__)

# ── App state ────────────────────────────────────────────────────

_config: Config
_browser: BrowserController
_mlb: MLBProvider
_session: MLBSession
_scheduler: GameScheduler

_now_playing_game_id: str | None = None
_now_playing_feed: str = "HOME"
_stream_info: StreamInfo | None = None
_play_lock: asyncio.Lock
_heartbeat_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None
_expiry_task: asyncio.Task | None = None
_browser_started_at: float = 0  # monotonic time
CHROME_RECYCLE_HOURS = 8  # restart Chrome after this many hours of idle


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config, _browser, _mlb, _session, _scheduler, _play_lock, _watchdog_task
    _config = Config()
    _browser = BrowserController(_config)
    _mlb = MLBProvider()
    _session = MLBSession()
    _scheduler = GameScheduler(_config)
    _scheduler.register_provider(_mlb)
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

    await _scheduler.start()

    # Start the watchdog
    _watchdog_task = asyncio.create_task(_watchdog_loop())

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

            # Proactively refresh auth before expiry
            if _session._username and not _session.is_authenticated:
                log.info("Watchdog: token expiring — refreshing...")
                await _session.ensure_authenticated()

        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Watchdog error")


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

    ok = await _browser.navigate("http://127.0.0.1:5000/player")
    if not ok:
        raise HTTPException(503, "Failed to navigate browser to player")

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
    await _browser.stop_playback()
    _now_playing_game_id = None
    _now_playing_feed = "HOME"
    _stream_info = None


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
    return {"url": _stream_info.url}


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


@app.get("/player", response_class=HTMLResponse)
async def player_page():
    return _PLAYER_HTML


_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/static/{filename}")
async def static_file(filename: str):
    path = _STATIC_DIR / filename
    if not path.is_file():
        raise HTTPException(404)
    media = "application/javascript" if filename.endswith(".js") else "application/octet-stream"
    return FileResponse(path, media_type=media)


# ── Player HTML (with auto-reconnect) ───────────────────────────

_PLAYER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>TV Automator Player</title>
<style>
  * { margin: 0; padding: 0; }
  body { background: #000; overflow: hidden; }
  video { width: 100vw; height: 100vh; object-fit: contain; }
  #status { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
            font-family: sans-serif; font-size: 1rem;
            background: rgba(0,0,0,0.85); padding: 10px 24px; border-radius: 8px;
            display: none; z-index: 10; transition: opacity 0.3s; }
  .error { color: #ef4444; }
  .info  { color: #8b92b3; }
</style>
</head>
<body>
<video id="video" autoplay></video>
<div id="status"></div>
<script src="/static/hls.min.js"></script>
<script>
(function() {
  const video = document.getElementById('video');
  const statusEl = document.getElementById('status');
  let hls = null;
  let retries = 0;
  let reconnecting = false;
  const MAX_RETRIES = 5;
  const RETRY_DELAYS = [3000, 5000, 10000, 20000, 30000];

  function showStatus(msg, cls) {
    statusEl.textContent = msg;
    statusEl.className = cls || '';
    statusEl.style.display = '';
  }
  function hideStatus() { statusEl.style.display = 'none'; }

  async function loadStream() {
    try {
      const res = await fetch('/api/stream');
      if (!res.ok) { showStatus('No stream available', 'error'); return; }
      const { url } = await res.json();
      startPlayer(url);
    } catch (e) {
      showStatus('Failed to load stream: ' + e.message, 'error');
      scheduleReconnect();
    }
  }

  function startPlayer(url) {
    if (hls) { hls.destroy(); hls = null; }

    if (!Hls.isSupported()) {
      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url;
        video.play();
        return;
      }
      showStatus('HLS not supported', 'error');
      return;
    }

    hls = new Hls({
      maxBufferLength: 30,
      maxMaxBufferLength: 120,
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 6,
      enableWorker: true,
    });
    hls.loadSource(url);
    hls.attachMedia(video);

    hls.on(Hls.Events.MANIFEST_PARSED, function() {
      video.play();
      retries = 0;
      hideStatus();
    });

    hls.on(Hls.Events.ERROR, function(_, data) {
      if (!data.fatal) return;

      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        showStatus('Network error \u2014 reconnecting...', 'info');
        // Try hls.js built-in recovery first
        hls.startLoad();
        // If still failing after 10s, do a full reconnect
        setTimeout(function() {
          if (hls && hls.media && hls.media.paused) {
            scheduleReconnect();
          }
        }, 10000);
      } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        showStatus('Media error \u2014 recovering...', 'info');
        hls.recoverMediaError();
      } else {
        showStatus('Fatal error \u2014 reconnecting...', 'error');
        scheduleReconnect();
      }
    });
  }

  async function scheduleReconnect() {
    if (reconnecting) return;
    if (retries >= MAX_RETRIES) {
      showStatus('Stream lost after ' + MAX_RETRIES + ' retries.', 'error');
      return;
    }
    reconnecting = true;
    retries++;
    const delay = RETRY_DELAYS[Math.min(retries - 1, RETRY_DELAYS.length - 1)];
    showStatus('Reconnecting (' + retries + '/' + MAX_RETRIES + ') in ' + (delay/1000) + 's...', 'info');
    await new Promise(r => setTimeout(r, delay));

    try {
      showStatus('Getting fresh stream...', 'info');
      const res = await fetch('/api/reconnect', { method: 'POST' });
      if (res.ok) {
        const { url } = await fetch('/api/stream').then(r => r.json());
        reconnecting = false;
        startPlayer(url);
      } else {
        reconnecting = false;
        showStatus('Reconnect failed \u2014 retrying...', 'error');
        scheduleReconnect();
      }
    } catch (e) {
      reconnecting = false;
      showStatus('Reconnect error: ' + e.message, 'error');
      scheduleReconnect();
    }
  }

  loadStream();
})();
</script>
</body>
</html>
"""

# ── Dashboard HTML ───────────────────────────────────────────────

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TV Automator</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #0f1117;
      --surface: #1a1d2e;
      --card: #1e2235;
      --border: #2d3148;
      --text: #e8eaf6;
      --muted: #8b92b3;
      --accent: #4f7dff;
      --green: #22c55e;
      --red: #ef4444;
      --yellow: #f59e0b;
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }

    header {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 14px 24px;
      display: flex;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
    }

    header h1 { font-size: 1.3rem; font-weight: 700; white-space: nowrap; }

    #now-playing-bar {
      flex: 1; font-size: 0.88rem; color: var(--green);
      font-weight: 600; display: none;
    }

    .controls {
      display: flex; align-items: center; gap: 10px;
      margin-left: auto; flex-wrap: wrap;
    }

    .date-nav { display: flex; align-items: center; gap: 8px; }
    .date-nav span { min-width: 220px; text-align: center; font-size: 0.9rem; font-weight: 600; }

    button {
      background: var(--card); border: 1px solid var(--border); color: var(--text);
      padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.82rem;
      line-height: 1.4; transition: background 0.12s;
    }
    button:hover { background: var(--border); }

    .btn-stop { background: var(--red); border-color: #c53030; color: #fff; }
    .btn-stop:hover { background: #c53030; }

    .btn-feed {
      padding: 6px 12px; font-size: 0.78rem; font-weight: 600;
      border-radius: 5px; cursor: pointer; transition: background 0.12s;
    }
    .btn-home { background: var(--accent); border-color: var(--accent); color: #fff; }
    .btn-home:hover { background: #3b6ae8; }
    .btn-away { background: var(--card); border-color: var(--border); color: var(--text); }
    .btn-away:hover { background: var(--border); }
    .btn-feed:disabled { background: var(--border); border-color: var(--border); color: var(--muted); cursor: default; }

    .auth-badge {
      font-size: 0.78rem; font-weight: 600; padding: 3px 10px;
      border-radius: 20px; white-space: nowrap;
    }
    .auth-ok  { background: rgba(34,197,94,0.15); color: var(--green); border: 1px solid rgba(34,197,94,0.3); }
    .auth-no  { background: rgba(239,68,68,0.12); color: var(--red);   border: 1px solid rgba(239,68,68,0.25); }

    main { padding: 24px; max-width: 1280px; margin: 0 auto; }

    #status-msg {
      text-align: center; color: var(--muted); padding: 60px 24px; font-size: 1rem;
    }

    #game-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px;
    }

    .game-card {
      background: var(--card); border: 1px solid var(--border); border-radius: 12px;
      padding: 18px 18px 14px; display: flex; flex-direction: column; gap: 10px;
      position: relative; transition: border-color 0.15s;
    }
    .game-card.live { border-color: var(--green); }
    .game-card.playing { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }

    .status-badge {
      position: absolute; top: 12px; right: 12px; padding: 2px 9px; border-radius: 20px;
      font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
    }
    .badge-live     { background: var(--green); color: #000; }
    .badge-pre_game { background: var(--yellow); color: #000; }
    .badge-scheduled { background: var(--border); color: var(--muted); }
    .badge-final    { background: transparent; color: var(--muted); border: 1px solid var(--border); }
    .badge-postponed, .badge-cancelled { background: var(--red); color: #fff; }
    .badge-unknown  { background: var(--border); color: var(--muted); }

    .matchup {
      display: flex; justify-content: space-between; align-items: center;
      padding: 6px 0 4px; gap: 8px;
    }
    .team { flex: 1; text-align: center; }
    .team-abbr { font-size: 1.9rem; font-weight: 800; line-height: 1; }
    .team-name {
      font-size: 0.68rem; color: var(--muted); margin-top: 3px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }

    .middle-col { display: flex; flex-direction: column; align-items: center; gap: 4px; min-width: 60px; }
    .score-row { display: flex; align-items: center; gap: 10px; }
    .score-val { font-size: 1.8rem; font-weight: 800; line-height: 1; }
    .score-sep { color: var(--muted); font-size: 1rem; }
    .at-sign { color: var(--muted); font-size: 0.85rem; font-weight: 600; }
    .inning-label { font-size: 0.75rem; color: var(--green); font-weight: 600; white-space: nowrap; }

    .pitchers {
      font-size: 0.72rem; color: var(--muted);
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }

    .card-footer { display: flex; justify-content: space-between; align-items: center; padding-top: 4px; }
    .game-time { font-size: 0.82rem; color: var(--muted); }
    .feed-btns { display: flex; gap: 6px; }
    .now-playing-tag { font-size: 0.8rem; color: var(--accent); font-weight: 600; }
  </style>
</head>
<body>
<header>
  <h1>TV Automator</h1>
  <div id="now-playing-bar">&#9654; Now playing: <span id="np-name"></span></div>
  <div class="controls">
    <span id="auth-badge" class="auth-badge"></span>
    <div class="date-nav">
      <button onclick="changeDate(-1)">&#9664;</button>
      <span id="date-label"></span>
      <button onclick="changeDate(1)">&#9654;</button>
    </div>
    <button id="today-btn" onclick="goToToday()" style="display:none">Today</button>
    <button id="stop-btn" class="btn-stop" onclick="stopPlayback()" style="display:none">&#9632; Stop</button>
    <button onclick="loadGames()">&#8635; Refresh</button>
  </div>
</header>
<main>
  <div id="status-msg">Loading...</div>
  <div id="game-grid"></div>
</main>
<script>
  let currentDate = new Date();
  currentDate.setHours(0, 0, 0, 0);
  let nowPlayingId = null;
  let refreshTimer = null;

  function pad(n) { return String(n).padStart(2, '0'); }
  function dateStr(d) { return d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate()); }

  function formatDateLabel(d) {
    const today = new Date(); today.setHours(0,0,0,0);
    const y = new Date(today); y.setDate(today.getDate()-1);
    const t = new Date(today); t.setDate(today.getDate()+1);
    let label;
    if (d.getTime()===today.getTime()) label='Today';
    else if (d.getTime()===y.getTime()) label='Yesterday';
    else if (d.getTime()===t.getTime()) label='Tomorrow';
    else label=d.toLocaleDateString('en-US',{weekday:'long'});
    return label+' \\u2014 '+d.toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'});
  }

  function changeDate(delta) { currentDate.setDate(currentDate.getDate()+delta); updateDateLabel(); loadGames(); }
  function goToToday() { currentDate=new Date(); currentDate.setHours(0,0,0,0); updateDateLabel(); loadGames(); }
  function updateDateLabel() {
    document.getElementById('date-label').textContent=formatDateLabel(currentDate);
    const today=new Date(); today.setHours(0,0,0,0);
    document.getElementById('today-btn').style.display=currentDate.getTime()===today.getTime()?'none':'';
  }

  async function loadGames() {
    clearTimeout(refreshTimer);
    document.getElementById('game-grid').innerHTML='';
    document.getElementById('status-msg').textContent='Loading...';
    document.getElementById('status-msg').style.display='';
    try {
      const [gRes,sRes]=await Promise.all([fetch('/api/games?date='+dateStr(currentDate)),fetch('/api/status')]);
      const games=await gRes.json(), status=await sRes.json();
      nowPlayingId=status.now_playing_game_id;
      updateStatus(games,status);
      if(!games.length){document.getElementById('status-msg').textContent='No games scheduled.';}
      else{document.getElementById('status-msg').style.display='none';renderGames(games);}
    } catch(e){document.getElementById('status-msg').textContent='Error: '+e.message;}
    refreshTimer=setTimeout(loadGames,30000);
  }

  function updateStatus(games,status) {
    const bar=document.getElementById('now-playing-bar'),stopBtn=document.getElementById('stop-btn');
    if(nowPlayingId){
      const g=games.find(x=>x.game_id===nowPlayingId);
      document.getElementById('np-name').textContent=g?g.display_matchup:nowPlayingId;
      bar.style.display='';stopBtn.style.display='';
    } else { bar.style.display='none';stopBtn.style.display='none'; }
    const badge=document.getElementById('auth-badge');
    badge.className='auth-badge '+(status.authenticated?'auth-ok':'auth-no');
    badge.textContent=status.authenticated?'\\u2713 Logged in':'\\u2717 No auth';
  }

  function renderGames(games){document.getElementById('game-grid').innerHTML=games.map(gameCardHTML).join('');}
  function badgeClass(s){return 'badge-'+(s==='pre_game'?'pre_game':s);}

  function gameCardHTML(g) {
    const isLive=g.status==='live', isPlaying=g.game_id===nowPlayingId;
    const canWatch=g.is_watchable||g.status==='final';
    const inning=g.extra&&g.extra.current_inning, inningState=g.extra&&g.extra.inning_state;
    const inningHTML=(isLive&&inning)?'<div class="inning-label">'+(inningState?inningState+' ':'')+inning+'</div>':'';

    let middleHTML;
    if(g.display_score){
      middleHTML='<div class="middle-col"><div class="score-row"><span class="score-val">'+g.away_team.score
        +'</span><span class="score-sep">\\u2014</span><span class="score-val">'+g.home_team.score
        +'</span></div>'+inningHTML+'</div>';
    } else { middleHTML='<div class="middle-col"><span class="at-sign">@</span></div>'; }

    const pp=[];
    if(g.extra&&g.extra.away_probable_pitcher) pp.push(g.away_team.abbreviation+': '+g.extra.away_probable_pitcher);
    if(g.extra&&g.extra.home_probable_pitcher) pp.push(g.home_team.abbreviation+': '+g.extra.home_probable_pitcher);
    const pitcherHTML=(!g.display_score&&pp.length)?'<div class="pitchers">'+pp.join(' \\u00b7 ')+'</div>':'';

    let actionHTML;
    if(isPlaying){
      actionHTML='<span class="now-playing-tag">&#9654; Playing</span>';
    } else if(canWatch){
      actionHTML='<div class="feed-btns">'
        +'<button class="btn-feed btn-away" onclick="playGame(\\''+g.game_id+'\\',\\'AWAY\\')">Away</button>'
        +'<button class="btn-feed btn-home" onclick="playGame(\\''+g.game_id+'\\',\\'HOME\\')">Home</button>'
        +'</div>';
    } else {
      actionHTML='<div class="feed-btns"><button class="btn-feed" disabled>Not available</button></div>';
    }

    const cls=['game-card',isLive?'live':'',isPlaying?'playing':''].filter(Boolean).join(' ');
    return '<div class="'+cls+'">'
      +'<span class="status-badge '+badgeClass(g.status)+'">'+g.status_label+'</span>'
      +'<div class="matchup">'
      +'<div class="team"><div class="team-abbr">'+g.away_team.abbreviation+'</div><div class="team-name">'+g.away_team.name+'</div></div>'
      +middleHTML
      +'<div class="team"><div class="team-abbr">'+g.home_team.abbreviation+'</div><div class="team-name">'+g.home_team.name+'</div></div>'
      +'</div>'
      +pitcherHTML
      +'<div class="card-footer"><span class="game-time">'+g.display_time+'</span>'+actionHTML+'</div>'
      +'</div>';
  }

  async function playGame(gameId,feed) {
    try {
      const res=await fetch('/api/play/'+gameId+'?date='+dateStr(currentDate)+'&feed='+feed,{method:'POST'});
      const data=await res.json();
      if(res.ok&&data.success){nowPlayingId=gameId;loadGames();}
      else{alert(data.detail||'Playback failed');}
    } catch(e){alert('Error: '+e.message);}
  }

  async function stopPlayback(){
    try{await fetch('/api/stop',{method:'POST'});nowPlayingId=null;loadGames();}
    catch(e){alert('Error: '+e.message);}
  }

  updateDateLabel();
  loadGames();
</script>
</body>
</html>
"""
