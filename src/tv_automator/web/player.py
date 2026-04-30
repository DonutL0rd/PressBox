"""Stream / HLS / player module.

Owns: _stream_info, _now_playing_game_id, _now_playing_feed,
      _player_levels, _player_command, _heartbeat_task, _expiry_task,
      _browser_started_at.

Routes (/api/stream, /hls/*, /api/player/*, /api/reconnect) are exposed
as ``router``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, HTTPException, Response

from tv_automator.providers.mlb_session import StreamInfo

if TYPE_CHECKING:
    from tv_automator.web.app import AppContext

log = logging.getLogger(__name__)

# ── Module state ─────────────────────────────────────────────────

_ctx: AppContext  # set by init()

_stream_info: StreamInfo | None = None
_now_playing_game_id: str | None = None
_now_playing_feed: str = "HOME"
_player_levels: list[dict] = []
_player_command: dict | None = None

_heartbeat_task: asyncio.Task | None = None
_expiry_task: asyncio.Task | None = None

_browser_started_at: float = 0


# ── Initialisation ───────────────────────────────────────────────

def init(ctx: AppContext) -> None:
    global _ctx
    _ctx = ctx


# ── Public state accessors ────────────────────────────────────────

def get_now_playing_game_id() -> str | None:
    return _now_playing_game_id


def get_now_playing_feed() -> str:
    return _now_playing_feed


def get_stream_info() -> StreamInfo | None:
    return _stream_info


def heartbeat_active() -> bool:
    return _heartbeat_task is not None and not _heartbeat_task.done()


def get_browser_started_at() -> float:
    return _browser_started_at


def set_browser_started_at(t: float) -> None:
    global _browser_started_at
    _browser_started_at = t


def clear_player_state() -> None:
    """Reset stream/player state without navigation or CEC — used by stop helpers."""
    global _now_playing_game_id, _now_playing_feed, _stream_info, _player_levels, _player_command
    _now_playing_game_id = None
    _now_playing_feed = "HOME"
    _stream_info = None
    _player_levels = []
    _player_command = None


# ── Heartbeat ─────────────────────────────────────────────────────

async def _heartbeat_loop() -> None:
    """Send periodic heartbeats to keep the MLB stream alive."""
    while True:
        info = _stream_info
        if not info or not info.heartbeat_url:
            return
        await asyncio.sleep(info.heartbeat_interval)
        ok = await _ctx.session.send_heartbeat(info.heartbeat_url)
        if ok:
            log.debug("Heartbeat OK")
        else:
            log.warning("Heartbeat failed — stream may drop soon")
            await _ctx.broadcast({
                "type": "error",
                "code": "heartbeat_failed",
                "message": "Stream heartbeat failed — video may freeze soon",
            })


def start_heartbeat() -> None:
    global _heartbeat_task
    stop_heartbeat()
    if _stream_info and _stream_info.heartbeat_url:
        _heartbeat_task = asyncio.create_task(_heartbeat_loop())
        log.info("Heartbeat started (every %ds)", _stream_info.heartbeat_interval)


def stop_heartbeat() -> None:
    global _heartbeat_task
    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()
        log.info("Heartbeat stopped")
    _heartbeat_task = None


# ── Expiry timer ──────────────────────────────────────────────────

async def _expiry_refresh_loop() -> None:
    """Proactively refresh the stream URL before it expires."""
    while _stream_info and _stream_info.expiration:
        now = datetime.now(timezone.utc)
        remaining = (_stream_info.expiration - now).total_seconds() - 120
        if remaining > 0:
            log.info("Stream expires in %.0fs — will refresh in %.0fs", remaining + 120, remaining)
            await asyncio.sleep(remaining)
        log.info("Proactively refreshing stream before expiry...")
        await do_reconnect()
        return


def start_expiry_timer() -> None:
    global _expiry_task
    stop_expiry_timer()
    if _stream_info and _stream_info.expiration:
        _expiry_task = asyncio.create_task(_expiry_refresh_loop())


def stop_expiry_timer() -> None:
    global _expiry_task
    if _expiry_task and not _expiry_task.done():
        _expiry_task.cancel()
    _expiry_task = None


# ── Condensed game helper ─────────────────────────────────────────

async def _get_condensed_url(game_id: str) -> str | None:
    """Fetch condensed game video URL from the public MLB Stats API."""
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
        for pb in playbacks:
            if "hls" in (pb.get("name") or "").lower():
                log.info("Found condensed HLS for game %s: %s", game_id, pb["url"][:80])
                return pb["url"]
        for pb in playbacks:
            name = (pb.get("name") or "").lower()
            if "mp4avc" in name or "highbit" in name:
                log.info("Found condensed MP4 for game %s: %s", game_id, pb["url"][:80])
                return pb["url"]
        if playbacks:
            log.info("Found condensed playback for game %s: %s", game_id, playbacks[0].get("url", "")[:80])
            return playbacks[0].get("url")

    log.warning("No condensed game found for game %s (%d items checked)", game_id, len(items))
    return None


# ── Play / reconnect ──────────────────────────────────────────────

async def do_play_condensed(game_id: str) -> StreamInfo:
    """Play a condensed game replay from the public MLB CDN (no auth needed)."""
    global _now_playing_game_id, _now_playing_feed, _stream_info

    url = await _get_condensed_url(game_id)
    if not url:
        raise HTTPException(404, "Condensed game not available — it may take a few hours after the game ends")

    info = StreamInfo(url=url, direct=True)
    _stream_info = info
    _now_playing_game_id = game_id
    _now_playing_feed = "CONDENSED"

    if _ctx.cec.enabled:
        await _ctx.cec.power_on()
        await _ctx.cec.set_active_source()

    if not _ctx.browser.is_running:
        await _ctx.browser.start()
        set_browser_started_at(time.monotonic())

    ok = await _ctx.browser.navigate("http://127.0.0.1:5000/player")
    if not ok:
        raise HTTPException(503, "Failed to navigate browser to player")

    await _ctx.broadcast_status()
    return info


async def do_play(game_id: str, feed: str) -> StreamInfo:
    """Get a stream and navigate Chrome to the player. Returns StreamInfo."""
    global _now_playing_game_id, _now_playing_feed, _stream_info

    if not await _ctx.session.ensure_authenticated():
        raise HTTPException(401, "Not authenticated — check MLB_USERNAME / MLB_PASSWORD in .env")

    info = await _ctx.session.get_stream_info(game_id, feed_type=feed)
    if not info:
        raise HTTPException(502, "Could not get stream URL — game may not be available yet")

    _stream_info = info
    _now_playing_game_id = game_id
    _now_playing_feed = feed
    start_heartbeat()
    start_expiry_timer()

    if _ctx.cec.enabled:
        await _ctx.cec.power_on()
        await _ctx.cec.set_active_source()

    ok = await _ctx.browser.navigate("http://127.0.0.1:5000/player")
    if not ok:
        raise HTTPException(503, "Failed to navigate browser to player")

    await _ctx.broadcast_status()
    return info


async def do_reconnect(schedule_retry: bool = True) -> StreamInfo | None:
    """Get a fresh stream URL for the current game and reload the player."""
    global _stream_info

    if not _now_playing_game_id:
        return None

    log.info("Reconnecting stream for game %s (feed=%s)…", _now_playing_game_id, _now_playing_feed)
    stop_heartbeat()
    stop_expiry_timer()

    try:
        if _now_playing_feed == "CONDENSED":
            url = await _get_condensed_url(_now_playing_game_id)
            if not url:
                log.error("Reconnect failed — condensed game not available")
                await _ctx.broadcast({
                    "type": "error", "code": "stream_error",
                    "message": "Stream reconnect failed — condensed game not available",
                })
                if schedule_retry:
                    asyncio.create_task(_reconnect_with_retry())
                return None
            info = StreamInfo(url=url, direct=True)
            _stream_info = info
        else:
            info = await _ctx.session.get_stream_info(_now_playing_game_id, _now_playing_feed)
            if not info:
                log.error("Reconnect failed — no stream URL")
                await _ctx.broadcast({
                    "type": "error", "code": "stream_error",
                    "message": "Stream reconnect failed — retrying…",
                })
                if schedule_retry:
                    asyncio.create_task(_reconnect_with_retry())
                return None
            _stream_info = info
            start_heartbeat()
            start_expiry_timer()
        await _ctx.browser.navigate("http://127.0.0.1:5000/player")
        log.info("Reconnected successfully")
        return info
    except Exception:
        log.exception("Reconnect failed")
        await _ctx.broadcast({
            "type": "error", "code": "stream_error",
            "message": "Stream reconnect failed — retrying…",
        })
        if schedule_retry:
            asyncio.create_task(_reconnect_with_retry())
        return None


async def _reconnect_with_retry() -> None:
    """Retry do_reconnect up to 3 times with 30-second delays."""
    for attempt in range(1, 4):
        await asyncio.sleep(30)
        if not _now_playing_game_id:
            return
        log.info("Reconnect retry %d/3 for game %s…", attempt, _now_playing_game_id)
        result = await do_reconnect(schedule_retry=False)
        if result:
            await _ctx.broadcast({
                "type": "info", "code": "stream_recovered",
                "message": "Stream reconnected successfully",
            })
            return
    log.error("Stream reconnect gave up after 3 attempts for game %s", _now_playing_game_id)
    await _ctx.broadcast({
        "type": "error", "code": "stream_dead",
        "message": "Stream could not reconnect — stop and restart playback",
    })


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


@router.get("/api/stream")
async def get_stream():
    if not _stream_info:
        raise HTTPException(404, "No stream active")
    if _stream_info.direct:
        return {"url": _stream_info.url}
    return {"url": "/hls/master.m3u8"}


@router.get("/hls/{path:path}")
async def hls_proxy(path: str):
    """Proxy HLS requests to MLB CDN to avoid CORS blocks in the browser."""
    if not _stream_info:
        raise HTTPException(404, "No stream active")

    stream_url = _stream_info.url
    base_url = stream_url.rsplit("/", 1)[0] + "/"
    upstream = stream_url if path == "master.m3u8" else urljoin(base_url, path)

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

    if path.endswith(".m3u8") or "mpegurl" in ct:
        text = content.decode()
        rewritten = []
        for line in text.splitlines():
            if line and not line.startswith("#"):
                rewritten.append("/hls/" + line)
            else:
                if 'URI="' in line and 'URI="http' not in line:
                    line = line.replace('URI="', 'URI="/hls/')
                rewritten.append(line)
        content = "\n".join(rewritten).encode()
        ct = "application/vnd.apple.mpegurl"

    return Response(content=content, media_type=ct)


@router.post("/api/player/levels")
async def post_player_levels(body: dict):
    """Player reports available HLS quality levels after manifest parse."""
    global _player_levels
    _player_levels = body.get("levels", [])
    return {"ok": True}


@router.get("/api/player/levels")
async def get_player_levels():
    """Dashboard reads available quality levels for the current stream."""
    return {"levels": _player_levels}


@router.post("/api/player/command")
async def post_player_command(body: dict):
    """Dashboard sends a command to the player (e.g. quality change)."""
    global _player_command
    _player_command = body
    await _ctx.broadcast({"type": "player_command", **body})
    return {"ok": True}


@router.get("/api/player/command")
async def get_player_command():
    """Player polls for a pending command. Clears after read."""
    global _player_command
    cmd = _player_command
    _player_command = None
    return cmd or {}


@router.post("/api/reconnect")
async def reconnect():
    """Get a fresh stream URL and reload the player. Called by player on errors."""
    async with _ctx.play_lock:
        info = await do_reconnect()
        if info:
            return {"success": True, "url": info.url}
        raise HTTPException(502, "Reconnect failed")
