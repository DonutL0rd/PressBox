"""YouTube playback and watch-history module.

Owns: _watch_history, _youtube_mode, _youtube_video_id, _progress_task,
      _suggested_cache.

Routes (/api/youtube/*, /api/screensaver) are exposed as ``router``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from tv_automator.web.app import AppContext

log = logging.getLogger(__name__)

# ── Module state ─────────────────────────────────────────────────

_ctx: AppContext  # set by init()

_watch_history: dict[str, dict] = {}

_youtube_mode: bool = False
_youtube_video_id: str | None = None

_progress_task: asyncio.Task | None = None

_suggested_cache: dict[str, list[dict]] = {}
_suggested_cache_time: float = 0
SUGGESTED_CACHE_TTL = 1800  # 30 minutes


# ── Initialisation ───────────────────────────────────────────────

def init(ctx: AppContext) -> None:
    global _ctx
    _ctx = ctx
    load_history()


def invalidate_suggested_cache() -> None:
    """Reset the suggested-channels cache so the next request re-fetches."""
    global _suggested_cache_time
    _suggested_cache_time = 0


# ── Watch history helpers ─────────────────────────────────────────

def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "/data"))


def _history_path() -> Path:
    return _data_dir() / "watch_history.json"


def load_history() -> None:
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


async def save_current_progress(completed: bool = False) -> None:
    """Read position from the browser and persist it."""
    if not _youtube_video_id:
        return
    raw = await _ctx.browser.evaluate("window.ytGetState ? window.ytGetState() : null")
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
        entry["position"] = 0.0
    entry["last_watched"] = datetime.now(timezone.utc).isoformat()
    _save_history()
    log.debug("Progress saved: %s %.0fs/%.0fs completed=%s",
              _youtube_video_id, position, duration, entry["completed"])


async def _progress_save_loop() -> None:
    """Save YouTube playback position to disk every 30 seconds."""
    while True:
        await asyncio.sleep(30)
        try:
            await save_current_progress()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Progress save error")


def start_progress_task() -> None:
    global _progress_task
    stop_progress_task()
    _progress_task = asyncio.create_task(_progress_save_loop())


def stop_progress_task() -> None:
    global _progress_task
    if _progress_task and not _progress_task.done():
        _progress_task.cancel()
    _progress_task = None


# ── Public state accessors ────────────────────────────────────────

def get_youtube_mode() -> bool:
    return _youtube_mode


def get_youtube_video_id() -> str | None:
    return _youtube_video_id


def clear_youtube_state() -> None:
    """Reset YouTube globals without navigating or touching locks."""
    global _youtube_mode, _youtube_video_id
    _youtube_mode = False
    _youtube_video_id = None


# ── URL helpers ───────────────────────────────────────────────────

def _extract_youtube_id(url: str) -> str | None:
    """Extract a YouTube video ID from common URL formats."""
    m = re.search(
        r'(?:youtube\.com/watch\?(?:[^&]*&)*v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)'
        r'([a-zA-Z0-9_-]{11})',
        url,
    )
    return m.group(1) if m else None


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


@router.post("/api/youtube")
async def play_youtube(body: dict):
    """Navigate the TV browser to a YouTube video."""
    global _youtube_mode, _youtube_video_id
    url = body.get("url", "").strip()
    video_id = _extract_youtube_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL — paste a youtube.com or youtu.be link")

    if not _ctx.browser.is_running:
        raise HTTPException(503, "Browser not running — check DISPLAY / X11")

    resume_pos = body.get("resume_position", 0)
    nav_url = f"http://127.0.0.1:5000/tv/youtube?v={video_id}"
    if resume_pos and resume_pos > 5:
        nav_url += f"&t={int(resume_pos)}"

    # Import player here to avoid circular imports at module load
    from tv_automator.web import player as _player
    from tv_automator.web import music as _music

    async with _ctx.play_lock:
        if _youtube_mode and _youtube_video_id:
            await save_current_progress()
        stop_progress_task()

        if _player.get_now_playing_game_id():
            _player.stop_heartbeat()
            _player.stop_expiry_timer()
            _player.clear_player_state()

        await _music.stop_music_internal()
        _youtube_mode = True
        _youtube_video_id = video_id
        await _ctx.browser.navigate(nav_url)

    async def _record():
        info = await _fetch_video_info(video_id)
        _history_record_start(video_id, info)
        start_progress_task()

    asyncio.create_task(_record())
    await _ctx.broadcast_status()
    log.info("YouTube: playing video %s (resume=%.0fs)", video_id, resume_pos)
    return {"success": True, "video_id": video_id}


@router.post("/api/screensaver")
async def show_screensaver(body: dict | None = None):
    """Navigate the TV browser to the screensaver."""
    global _youtube_mode, _youtube_video_id
    completed = (body or {}).get("completed", False)
    from tv_automator.web import player as _player
    async with _ctx.play_lock:
        if _player.get_now_playing_game_id():
            await _ctx.do_stop()
        else:
            if _youtube_mode:
                stop_progress_task()
                await save_current_progress(completed=completed)
            _youtube_mode = False
            _youtube_video_id = None
            if _ctx.browser.is_running:
                await _ctx.browser.navigate("http://127.0.0.1:5000/screensaver")
            await _ctx.broadcast_status()
    return {"success": True}


@router.get("/api/youtube/history")
async def get_youtube_history():
    entries = sorted(_watch_history.values(), key=lambda e: e.get("last_watched", ""), reverse=True)
    return entries


@router.delete("/api/youtube/history/{video_id}")
async def delete_youtube_history(video_id: str):
    _watch_history.pop(video_id, None)
    _save_history()
    return {"success": True}


@router.get("/api/youtube/suggested")
async def get_suggested_videos():
    """Return recent videos from curated YouTube channels via public RSS feeds."""
    global _suggested_cache, _suggested_cache_time
    now = time.monotonic()
    if _suggested_cache and (now - _suggested_cache_time) < SUGGESTED_CACHE_TTL:
        return _suggested_cache

    results: dict[str, list[dict]] = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for channel_id, channel_name in _ctx.settings.get("suggested_channels", {}).items():
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


@router.get("/api/youtube/state")
async def youtube_state():
    """Return current YouTube player state (time, duration, paused, volume)."""
    if not _youtube_mode:
        return {"state": -1, "currentTime": 0, "duration": 0, "volume": 100, "muted": False}
    raw = await _ctx.browser.evaluate("window.ytGetState ? window.ytGetState() : null")
    if raw:
        return json.loads(raw)
    return {"state": -1, "currentTime": 0, "duration": 0, "volume": 100, "muted": False}


@router.post("/api/youtube/command")
async def youtube_command(body: dict):
    """Send a playback command to the YouTube player running in Chrome."""
    if not _youtube_mode:
        raise HTTPException(400, "YouTube mode not active")
    cmd = body.get("cmd")
    simple = {
        "play": "window.ytPlay()",
        "pause": "window.ytPause()",
        "mute": "window.ytMute()",
        "unmute": "window.ytUnmute()",
    }
    if cmd in simple:
        await _ctx.browser.evaluate(simple[cmd])
    elif cmd == "seek":
        t = float(body.get("time", 0))
        await _ctx.browser.evaluate(f"window.ytSeek({t})")
    elif cmd == "volume":
        v = max(0, min(100, int(body.get("volume", 50))))
        await _ctx.browser.evaluate(f"window.ytSetVolume({v})")
    elif cmd == "speed":
        allowed = {0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0}
        r = float(body.get("rate", 1.0))
        if r not in allowed:
            r = 1.0
        await _ctx.browser.evaluate(f"window.ytSetSpeed({r})")
    elif cmd == "cc":
        on = bool(body.get("enabled", False))
        await _ctx.browser.evaluate(f"window.ytSetCC({'true' if on else 'false'})")
    else:
        raise HTTPException(400, f"Unknown command: {cmd}")
    return {"success": True}
