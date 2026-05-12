"""Music playback module — mpv + Navidrome/Subsonic integration.

All server-side music state lives here.  The module is initialised once from
the lifespan via ``init(ctx)``.  Routes are exposed as ``router`` which the
main app wires in with ``app.include_router(router)``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from tv_automator.web.app import AppContext

log = logging.getLogger(__name__)

# ── Module state ─────────────────────────────────────────────────

_ctx: AppContext  # set by init()

_music_state = {
    "is_playing": False,
    "is_paused": False,
    "start_time": 0.0,         # monotonic time when song started/resumed
    "position_offset": 0.0,    # accumulated time from previous play segments
    "queue": [],
    "index": -1,
    "shuffle": False,
    "repeat": "off",
    "shuffle_order": [],
}

_mpv_proc: asyncio.subprocess.Process | None = None
_mpv_socket = "/tmp/mpv-music.sock"
_music_watcher_task: asyncio.Task | None = None
_music_lock: asyncio.Lock

_last_music_broadcast: str = ""


# ── Initialisation ───────────────────────────────────────────────

def init(ctx: AppContext) -> None:
    global _ctx, _music_lock
    _ctx = ctx
    _music_lock = asyncio.Lock()


# ── Subsonic auth helpers ─────────────────────────────────────────

def _subsonic_params() -> dict[str, str] | None:
    """Build Subsonic API auth query params. Returns None if not configured."""
    creds = _ctx.settings.navidrome_credentials
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
    server_url = _ctx.settings.navidrome_credentials[0].rstrip("/")
    return f"{server_url}/rest/stream?{urlencode(params)}"


async def _navidrome_api(endpoint: str, extra_params: dict | None = None) -> dict:
    """Proxy a Subsonic API call and return the parsed subsonic-response."""
    params = _subsonic_params()
    if not params:
        raise HTTPException(503, "Navidrome not configured")
    if extra_params:
        params.update(extra_params)
    server_url = _ctx.settings.navidrome_credentials[0].rstrip("/")
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


# ── mpv IPC helpers ───────────────────────────────────────────────

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
    resp = await _mpv_command("get_property", prop)
    if resp and "data" in resp:
        return resp["data"]
    return None


async def _mpv_set_property(prop: str, value):
    return await _mpv_command("set_property", prop, value)


async def _mpv_start(url: str) -> None:
    """Start or restart mpv with a new audio URL."""
    global _mpv_proc
    await _mpv_stop()
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


# ── Queue helpers ─────────────────────────────────────────────────

def _music_build_shuffle_order() -> None:
    _music_state["shuffle_order"] = list(range(len(_music_state["queue"])))
    random.shuffle(_music_state["shuffle_order"])
    if _music_state["index"] in _music_state["shuffle_order"]:
        _music_state["shuffle_order"].remove(_music_state["index"])
        _music_state["shuffle_order"].insert(0, _music_state["index"])


async def _music_play_current() -> None:
    idx = _music_state["index"]
    if idx < 0 or idx >= len(_music_state["queue"]):
        return
    song = _music_state["queue"][idx]
    url = _subsonic_stream_url(song["id"])
    if not url:
        return
    await _mpv_start(url)


async def _music_advance(direction: int = 1) -> dict | None:
    if not _music_state["queue"]:
        return None

    if _music_state["shuffle"] and _music_state["shuffle_order"]:
        cur = _music_state["shuffle_order"].index(_music_state["index"]) if _music_state["index"] in _music_state["shuffle_order"] else 0
        nxt = cur + direction
        if nxt < 0 or nxt >= len(_music_state["shuffle_order"]):
            if _music_state["repeat"] == "all":
                nxt = nxt % len(_music_state["shuffle_order"])
            else:
                return None
        _music_state["index"] = _music_state["shuffle_order"][nxt]
    else:
        _music_state["index"] += direction
        if _music_state["index"] < 0 or _music_state["index"] >= len(_music_state["queue"]):
            if _music_state["repeat"] == "all":
                _music_state["index"] = _music_state["index"] % len(_music_state["queue"])
            else:
                _music_state["index"] = max(0, min(_music_state["index"], len(_music_state["queue"]) - 1))
                return None

    if _ctx.browser.is_running:
        await _music_play_current()
    return _music_state["queue"][_music_state["index"]]


# ── Broadcast helpers ─────────────────────────────────────────────

async def _broadcast_music_status(force: bool = False) -> None:
    global _last_music_broadcast
    data = await get_status()
    song = data.get('song')
    song_id = song.get('id', '') if song else ''
    key = f"{data.get('playing')}|{data.get('paused')}|{song_id}|{int(data.get('position', 0))}"
    if not force and key == _last_music_broadcast:
        return
    _last_music_broadcast = key
    await _ctx.broadcast({"type": "music", **data})


async def _broadcast_music_queue() -> None:
    await _ctx.broadcast({"type": "queue", "songs": _music_state["queue"], "index": _music_state["index"]})


# ── Playback watcher ──────────────────────────────────────────────

async def _music_watch_playback() -> None:
    """Watch mpv or remote kiosk for track end and auto-advance the queue."""
    _tick = 0
    while True:
        await asyncio.sleep(1)
        _tick += 1

        should_advance = False
        if _ctx.browser.is_running:
            # Local mpv logic
            local_proc_done = not _mpv_proc or _mpv_proc.returncode is not None
            idle = False
            if not local_proc_done:
                idle = await _mpv_get_property("idle-active") or False
            if local_proc_done or idle:
                should_advance = True
        else:
            # Remote kiosk logic
            from tv_automator.web import app
            remote_state = getattr(app, "_remote_music_state", {})
            if remote_state:
                pos = remote_state.get("position", 0)
                dur = remote_state.get("duration", 0)
                # Advance if within 3s of end (more lenient for remote network jitter)
                if dur > 5 and pos > (dur - 3):
                    should_advance = True
                    app._remote_music_state = {}

        if not should_advance:
            if not _music_state["is_playing"]:
                break
            if _tick % 2 == 0 and _ctx.ws_clients:
                await _broadcast_music_status()
            continue

        # Advancement logic
        if not _music_state["queue"]:
            break
        async with _music_lock:
            if _music_state["repeat"] == "one":
                if _ctx.browser.is_running: await _music_play_current()
                continue
            result = await _music_advance(1)
            if result:
                log.info("Music: automatically advancing to next track: %s", result.get("title"))
            else:
                _music_state["is_playing"] = False
                break
        await _broadcast_music_queue()
        await _broadcast_music_status()


def _music_start_watcher() -> None:
    global _music_watcher_task
    if _music_watcher_task and not _music_watcher_task.done():
        _music_watcher_task.cancel()
    _music_watcher_task = asyncio.create_task(_music_watch_playback())


# ── Public API ────────────────────────────────────────────────────

async def stop_music_internal() -> None:
    """Stop music playback and clear the queue. Safe to call from app.py."""
    global _music_watcher_task
    _music_state["is_playing"] = False
    if _music_watcher_task and not _music_watcher_task.done():
        _music_watcher_task.cancel()
        _music_watcher_task = None
    async with _music_lock:
        await _mpv_stop()
        _music_state["queue"].clear()
        _music_state["index"] = -1


async def get_status() -> dict:
    """Return current music playback state (used by WS endpoint and dashboard)."""
    local_playing = _mpv_proc is not None and _mpv_proc.returncode is None
    idx = _music_state["index"]
    song = _music_state["queue"][idx] if 0 <= idx < len(_music_state["queue"]) else None
    
    # Intention to play
    playing = local_playing or _music_state["is_playing"]
    
    # Calculate position from master clock
    position = _music_state["position_offset"]
    if playing and not _music_state["is_paused"]:
        position += time.monotonic() - _music_state["start_time"]
    
    # Cap position at song duration if known
    duration = song.get("duration", 0) if song else 0
    if duration > 0:
        position = min(position, float(duration))

    result: dict = {
        "playing": playing,
        "song": song,
        "queue_length": len(_music_state["queue"]),
        "queue_index": _music_state["index"],
        "shuffle": _music_state["shuffle"],
        "repeat": _music_state["repeat"],
        "position": round(position, 2),
        "duration": duration,
        "paused": _music_state["is_paused"],
    }
    
    if local_playing:
        # Override with local mpv data if it's actually running here
        result["position"] = await _mpv_get_property("time-pos") or 0
        result["duration"] = await _mpv_get_property("duration") or 0
        result["paused"] = await _mpv_get_property("pause") or False
        result["volume"] = await _mpv_get_property("volume") or 100

    return result


def get_queue_state() -> dict:
    return {"songs": _music_state["queue"], "index": _music_state["index"]}


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


@router.get("/api/music/proxy/{song_id}")
async def music_proxy(song_id: str):
    """Proxy the music stream from Navidrome to avoid CORS/connectivity issues."""
    url = _subsonic_stream_url(song_id)
    if not url:
        raise HTTPException(503, "Navidrome not configured")
    
    async def stream_generator():
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("GET", url) as response:
                async for chunk in response.aiter_bytes():
                    yield chunk

    return StreamingResponse(stream_generator(), media_type="audio/mpeg")


@router.get("/api/music/ping")
async def music_ping():
    """Test Navidrome connection."""
    try:
        resp = await _navidrome_api("/rest/ping")
        return {"ok": True, "version": resp.get("version", "?")}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/music/credentials")
async def music_credentials(body: dict):
    """Save Navidrome credentials and verify connection."""
    server_url = body.get("server_url", "").strip().rstrip("/")
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    if not server_url or not username or not password:
        raise HTTPException(400, "All fields are required")
    _ctx.settings.update({
        "navidrome_server_url": server_url,
        "navidrome_username": username,
        "navidrome_password": password,
    })
    _ctx.settings.save()
    try:
        resp = await _navidrome_api("/rest/ping")
        log.info("Navidrome connected: %s@%s", username, server_url)
        return {"success": True, "version": resp.get("version", "?")}
    except Exception as e:
        log.warning("Navidrome credentials saved but ping failed: %s", e)
        return {"success": False, "error": str(e)}


@router.get("/api/music/artists")
async def music_artists():
    resp = await _navidrome_api("/rest/getArtists")
    return resp.get("artists", {})


@router.get("/api/music/albums")
async def music_albums(type: str = "recent", size: int = 40, offset: int = 0):
    resp = await _navidrome_api("/rest/getAlbumList2", {
        "type": type, "size": str(size), "offset": str(offset),
    })
    return resp.get("albumList2", {})


@router.get("/api/music/album/{album_id}")
async def music_album(album_id: str):
    resp = await _navidrome_api("/rest/getAlbum", {"id": album_id})
    return resp.get("album", {})


@router.get("/api/music/artist/{artist_id}")
async def music_artist(artist_id: str):
    resp = await _navidrome_api("/rest/getArtist", {"id": artist_id})
    return resp.get("artist", {})


@router.get("/api/music/search")
async def music_search(query: str = "", artistCount: int = 5, albumCount: int = 10, songCount: int = 20):
    resp = await _navidrome_api("/rest/search3", {
        "query": query,
        "artistCount": str(artistCount),
        "albumCount": str(albumCount),
        "songCount": str(songCount),
    })
    return resp.get("searchResult3", {})


@router.get("/api/music/playlists")
async def music_playlists():
    resp = await _navidrome_api("/rest/getPlaylists")
    return resp.get("playlists", {})


@router.get("/api/music/playlist/{playlist_id}")
async def music_playlist(playlist_id: str):
    resp = await _navidrome_api("/rest/getPlaylist", {"id": playlist_id})
    return resp.get("playlist", {})


@router.get("/api/music/radio")
async def music_radio():
    resp = await _navidrome_api("/rest/getInternetRadioStations")
    return resp.get("internetRadioStations", {})


@router.get("/api/music/cover/{item_id}")
async def music_cover(item_id: str, size: int = 300):
    """Proxy album art from Navidrome with browser caching."""
    params = _subsonic_params()
    if not params:
        raise HTTPException(503, "Navidrome not configured")
    params["id"] = item_id
    params["size"] = str(size)
    params.pop("f", None)
    server_url = _ctx.settings.navidrome_credentials[0].rstrip("/")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{server_url}/rest/getCoverArt", params=params)
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Cover art not found")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/music/stream/{song_id}")
async def music_stream(song_id: str):
    """Return a direct Navidrome stream URL (authenticated)."""
    url = _subsonic_stream_url(song_id)
    if not url:
        raise HTTPException(503, "Navidrome not configured")
    return {"url": url}


@router.post("/api/music/play")
async def music_play(body: dict):
    """Start playing a queue of songs. Body: {songs: [...], index: 0}."""
    songs = body.get("songs", [])
    index = body.get("index", 0)
    if not songs:
        raise HTTPException(400, "No songs provided")
    async with _ctx.play_lock:
        await _ctx.stop_video_for_music()
    async with _music_lock:
        _music_state["queue"][:] = [
            {
                "id": s["id"],
                "title": s.get("title", ""),
                "artist": s.get("artist", ""),
                "albumId": s.get("albumId") or s.get("parent", ""),
                "duration": s.get("duration", 0),
            }
            for s in songs
        ]
        _music_state["index"] = index
        _music_state["is_playing"] = True
        _music_state["is_paused"] = False
        _music_state["start_time"] = time.monotonic()
        _music_state["position_offset"] = 0.0
        
        if _music_state["shuffle"]:
            _music_build_shuffle_order()
        
        if _ctx.browser.is_running:
            await _music_play_current()
            
        _music_start_watcher()
            
    await _broadcast_music_status()
    await _broadcast_music_queue()
    return {"playing": True, "song": _music_state["queue"][_music_state["index"]]}


@router.post("/api/music/command")
async def music_command(body: dict):
    """Send a transport command. Body: {command: pause|resume|next|prev|stop|seek|…}."""
    cmd = body.get("command", "")
    result: dict = {"ok": True}
    queue_changed = False

    if cmd == "pause":
        _music_state["is_paused"] = True
        if _ctx.browser.is_running: await _mpv_set_property("pause", True)
        await _ctx.broadcast({"type": "music_command", "cmd": "pause"})
    elif cmd == "resume":
        _music_state["is_paused"] = False
        if _ctx.browser.is_running: await _mpv_set_property("pause", False)
        await _ctx.broadcast({"type": "music_command", "cmd": "play"})
    elif cmd == "toggle":
        new_paused = not _music_state["is_paused"]
        _music_state["is_paused"] = new_paused
        if _ctx.browser.is_running:
            await _mpv_set_property("pause", new_paused)
        await _ctx.broadcast({"type": "music_command", "cmd": "pause" if new_paused else "play"})
        result["paused"] = new_paused
    elif cmd == "next":
        async with _music_lock:
            result["song"] = await _music_advance(1)
            _music_start_watcher()
            _music_state["is_playing"] = True
            _music_state["is_paused"] = False
        queue_changed = True
    elif cmd == "prev":
        if _ctx.browser.is_running:
            pos = await _mpv_get_property("time-pos")
            if pos and pos > 3:
                await _mpv_command("seek", 0, "absolute")
            else:
                async with _music_lock:
                    result["song"] = await _music_advance(-1)
                    _music_start_watcher()
                    _music_state["is_paused"] = False
                queue_changed = True
        else:
            async with _music_lock:
                result["song"] = await _music_advance(-1)
                _music_state["is_playing"] = True
                _music_state["is_paused"] = False
                _music_start_watcher()
            queue_changed = True
    elif cmd == "stop":
        _music_state["is_playing"] = False
        _music_state["is_paused"] = False
        await stop_music_internal()
        queue_changed = True
    elif cmd == "seek":
        val = body.get("value", 0)
        from tv_automator.web import app
        app._remote_music_state = {} # Clear stale cache
        if _ctx.browser.is_running: await _mpv_command("seek", val, "absolute")
        await _ctx.broadcast({"type": "music_command", "cmd": "seek", "value": val})
    elif cmd == "volume":
        val = max(0, min(100, float(body.get("value", 100))))
        if _ctx.browser.is_running: await _mpv_set_property("volume", val)
        await _ctx.broadcast({"type": "volume", "volume": val})
    elif cmd == "jump":
        idx = int(body.get("value", 0))
        async with _music_lock:
            if 0 <= idx < len(_music_state["queue"]):
                _music_state["index"] = idx
                if _ctx.browser.is_running:
                    await _music_play_current()
                _music_start_watcher()
                _music_state["is_playing"] = True
                _music_state["is_paused"] = False
                queue_changed = True
            else:
                raise HTTPException(400, "Invalid queue index")
    elif cmd == "shuffle":
        async with _music_lock:
            _music_state["shuffle"] = body.get("value", not _music_state["shuffle"])
            if _music_state["shuffle"]:
                _music_build_shuffle_order()
            else:
                _music_state["shuffle_order"].clear()
            result["shuffle"] = _music_state["shuffle"]
    elif cmd == "repeat":
        async with _music_lock:
            modes = ["off", "all", "one"]
            idx = modes.index(_music_state["repeat"]) if _music_state["repeat"] in modes else 0
            _music_state["repeat"] = modes[(idx + 1) % 3]
            result["repeat"] = _music_state["repeat"]
    else:
        raise HTTPException(400, f"Unknown command: {cmd}")

    await _broadcast_music_status(force=True)
    if queue_changed:
        await _broadcast_music_queue()
    return result


@router.get("/api/music/status")
async def music_status_route():
    """Get current music playback state from the server-side mpv player."""
    return await get_status()


@router.get("/api/music/sinks")
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


@router.post("/api/music/sink")
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
        if _mpv_proc and _mpv_proc.returncode is None:
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


@router.get("/api/music/queue")
async def music_get_queue():
    """Return the current playback queue and active index."""
    return get_queue_state()


@router.post("/api/music/queue/append")
async def music_queue_append(body: dict):
    """Append songs to the queue without stopping current playback."""
    songs = body.get("songs", [])
    if not songs:
        raise HTTPException(400, "No songs provided")
    need_start = False
    async with _music_lock:
        for s in songs:
            _music_state["queue"].append({
                "id": s["id"], "title": s.get("title", ""), "artist": s.get("artist", ""),
                "albumId": s.get("albumId") or s.get("parent", ""), "duration": s.get("duration", 0),
            })
        if _music_state["shuffle"]:
            _music_build_shuffle_order()
        if _music_state["index"] < 0:
            need_start = True
            _music_state["index"] = 0
            _music_state["is_playing"] = True
            _music_state["is_paused"] = False
            _music_state["start_time"] = time.monotonic()
            _music_state["position_offset"] = 0.0
    if need_start:
        async with _ctx.play_lock:
            await _ctx.stop_video_for_music()
        async with _music_lock:
            if _ctx.browser.is_running:
                await _music_play_current()
            _music_start_watcher()
    await _broadcast_music_queue()
    await _broadcast_music_status()
    return {"ok": True, "queue_length": len(_music_state["queue"])}


@router.post("/api/music/queue/remove")
async def music_queue_remove(body: dict):
    """Remove a song from the queue by index."""
    idx = body.get("index")
    if idx is None or idx < 0 or idx >= len(_music_state["queue"]):
        raise HTTPException(400, "Invalid index")
    async with _music_lock:
        _music_state["queue"].pop(idx)
        if _music_state["shuffle"]:
            _music_build_shuffle_order()
        if idx < _music_state["index"]:
            _music_state["index"] -= 1
        elif idx == _music_state["index"]:
            if _music_state["queue"]:
                _music_state["index"] = min(_music_state["index"], len(_music_state["queue"]) - 1)
                if _ctx.browser.is_running:
                    await _music_play_current()
                _music_start_watcher()
            else:
                _music_state["index"] = -1
                await _mpv_stop()
    await _broadcast_music_queue()
    await _broadcast_music_status()
    return {"ok": True, "queue_length": len(_music_state["queue"])}


@router.get("/api/music/starred")
async def music_starred():
    """Get starred (favourited) songs, albums, and artists."""
    resp = await _navidrome_api("/rest/getStarred2")
    return resp.get("starred2", {})


@router.post("/api/music/star")
async def music_star(body: dict):
    """Star or unstar a song/album/artist."""
    item_id = body.get("id")
    action = body.get("action", "star")
    if not item_id:
        raise HTTPException(400, "ID required")
    endpoint = "/rest/star" if action == "star" else "/rest/unstar"
    await _navidrome_api(endpoint, {"id": item_id})
    return {"ok": True}
