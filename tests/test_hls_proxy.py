"""Tests for the HLS proxy — URL rewriting, stream endpoint, player command API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tv_automator.web.player as player_module
from tv_automator.providers.mlb_session import StreamInfo


# ── App fixture ───────────────────────────────────────────────────

@pytest.fixture
def player_app():
    app = FastAPI()
    app.include_router(player_module.router)
    return app


@pytest.fixture(autouse=True)
def reset_player_state():
    player_module.clear_player_state()
    player_module._player_levels = []
    player_module._player_command = None

    ctx = MagicMock()
    ctx.broadcast = AsyncMock()
    ctx.play_lock = MagicMock()
    ctx.play_lock.__aenter__ = AsyncMock(return_value=None)
    ctx.play_lock.__aexit__ = AsyncMock(return_value=None)
    player_module._ctx = ctx

    yield

    player_module.clear_player_state()
    player_module.stop_heartbeat()
    player_module.stop_expiry_timer()


def set_active_stream(url: str, direct: bool = False):
    player_module._stream_info = StreamInfo(url=url, direct=direct)
    player_module._now_playing_game_id = "test_game"


def make_upstream_mock(content: bytes, content_type: str = "application/octet-stream"):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    resp.headers = {"content-type": content_type}

    client_mock = MagicMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)
    client_mock.get = AsyncMock(return_value=resp)
    return client_mock


# ── GET /api/stream ───────────────────────────────────────────────

def test_get_stream_returns_404_when_no_stream(player_app):
    client = TestClient(player_app, raise_server_exceptions=False)
    resp = client.get("/api/stream")
    assert resp.status_code == 404


def test_get_stream_returns_proxy_url_for_live_stream(player_app):
    set_active_stream("https://hls.example.com/live/master.m3u8")
    client = TestClient(player_app)
    resp = client.get("/api/stream")
    assert resp.status_code == 200
    assert resp.json()["url"] == "/hls/master.m3u8"


def test_get_stream_returns_direct_url_for_condensed(player_app):
    set_active_stream("https://cdn.example.com/condensed.mp4", direct=True)
    client = TestClient(player_app)
    resp = client.get("/api/stream")
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://cdn.example.com/condensed.mp4"


# ── GET /hls/* ────────────────────────────────────────────────────

def test_hls_proxy_returns_404_when_no_stream(player_app):
    client = TestClient(player_app, raise_server_exceptions=False)
    resp = client.get("/hls/master.m3u8")
    assert resp.status_code == 404


def test_hls_proxy_rewrites_relative_urls_in_m3u8(player_app):
    set_active_stream("https://hls.example.com/live/master.m3u8")

    m3u8 = "\n".join([
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000",
        "chunklist_1080p.m3u8",
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000",
        "chunklist_720p.m3u8",
    ])
    upstream = make_upstream_mock(m3u8.encode(), "application/vnd.apple.mpegurl")

    with patch("tv_automator.web.player.httpx.AsyncClient", return_value=upstream):
        client = TestClient(player_app)
        resp = client.get("/hls/master.m3u8")

    assert resp.status_code == 200
    body = resp.text
    assert "/hls/chunklist_1080p.m3u8" in body
    assert "/hls/chunklist_720p.m3u8" in body
    # Comment lines must be preserved verbatim
    assert "#EXTM3U" in body
    assert "#EXT-X-VERSION:3" in body
    assert "#EXT-X-STREAM-INF:BANDWIDTH=5000000" in body


def test_hls_proxy_rewrites_uri_attribute_in_ext_tags(player_app):
    set_active_stream("https://hls.example.com/live/master.m3u8")

    m3u8 = '#EXT-X-KEY:METHOD=AES-128,URI="key.bin",IV=0x00\nsegment.ts\n'
    upstream = make_upstream_mock(m3u8.encode(), "application/vnd.apple.mpegurl")

    with patch("tv_automator.web.player.httpx.AsyncClient", return_value=upstream):
        client = TestClient(player_app)
        resp = client.get("/hls/master.m3u8")

    assert resp.status_code == 200
    body = resp.text
    assert '/hls/key.bin' in body
    assert "/hls/segment.ts" in body


def test_hls_proxy_does_not_rewrite_absolute_uri_attributes(player_app):
    """URIs that are already absolute (http/https) should not be double-prefixed."""
    set_active_stream("https://hls.example.com/live/master.m3u8")

    m3u8 = '#EXT-X-KEY:METHOD=AES-128,URI="https://keys.example.com/key.bin"\n'
    upstream = make_upstream_mock(m3u8.encode(), "application/vnd.apple.mpegurl")

    with patch("tv_automator.web.player.httpx.AsyncClient", return_value=upstream):
        client = TestClient(player_app)
        resp = client.get("/hls/master.m3u8")

    body = resp.text
    # The absolute URI should not have /hls/ prepended
    assert 'URI="https://keys.example.com/key.bin"' in body


def test_hls_proxy_passes_through_binary_segments(player_app):
    set_active_stream("https://hls.example.com/live/master.m3u8")

    segment_data = b"\x00\x01\x02\x03binary_ts_data"
    upstream = make_upstream_mock(segment_data, "video/mp2t")

    with patch("tv_automator.web.player.httpx.AsyncClient", return_value=upstream):
        client = TestClient(player_app)
        resp = client.get("/hls/segment_0.ts")

    assert resp.status_code == 200
    assert resp.content == segment_data


def test_hls_proxy_returns_502_when_upstream_fetch_fails(player_app):
    set_active_stream("https://hls.example.com/live/master.m3u8")

    import httpx as httpx_mod
    client_mock = MagicMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)
    client_mock.get = AsyncMock(side_effect=httpx_mod.NetworkError("down"))

    with patch("tv_automator.web.player.httpx.AsyncClient", return_value=client_mock):
        client = TestClient(player_app, raise_server_exceptions=False)
        resp = client.get("/hls/master.m3u8")

    assert resp.status_code == 502


def test_hls_proxy_returns_upstream_error_code(player_app):
    set_active_stream("https://hls.example.com/live/master.m3u8")

    err_resp = MagicMock()
    err_resp.status_code = 403
    err_resp.content = b"Forbidden"
    err_resp.headers = {"content-type": "text/plain"}

    client_mock = MagicMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)
    client_mock.get = AsyncMock(return_value=err_resp)

    with patch("tv_automator.web.player.httpx.AsyncClient", return_value=client_mock):
        test_client = TestClient(player_app, raise_server_exceptions=False)
        resp = test_client.get("/hls/master.m3u8")

    assert resp.status_code == 403


# ── /api/player/levels ────────────────────────────────────────────

def test_post_player_levels_stores_and_returns_ok(player_app):
    client = TestClient(player_app)
    resp = client.post("/api/player/levels", json={"levels": [{"height": 1080}, {"height": 720}]})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_get_player_levels_returns_stored_levels(player_app):
    client = TestClient(player_app)
    client.post("/api/player/levels", json={"levels": [{"height": 1080}]})
    resp = client.get("/api/player/levels")
    assert resp.status_code == 200
    assert len(resp.json()["levels"]) == 1
    assert resp.json()["levels"][0]["height"] == 1080


def test_get_player_levels_empty_initially(player_app):
    client = TestClient(player_app)
    resp = client.get("/api/player/levels")
    assert resp.status_code == 200
    assert resp.json()["levels"] == []


# ── /api/player/command ───────────────────────────────────────────

def test_post_player_command_stores_and_broadcasts(player_app):
    client = TestClient(player_app)
    resp = client.post("/api/player/command", json={"action": "quality", "level": 2})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_get_player_command_returns_pending_command(player_app):
    client = TestClient(player_app)
    client.post("/api/player/command", json={"action": "quality", "level": 2})
    resp = client.get("/api/player/command")
    assert resp.status_code == 200
    assert resp.json()["action"] == "quality"
    assert resp.json()["level"] == 2


def test_get_player_command_clears_after_read(player_app):
    client = TestClient(player_app)
    client.post("/api/player/command", json={"action": "quality", "level": 2})
    client.get("/api/player/command")  # consume
    resp = client.get("/api/player/command")  # should be empty now
    assert resp.status_code == 200
    assert resp.json() == {}


def test_get_player_command_returns_empty_when_none(player_app):
    client = TestClient(player_app)
    resp = client.get("/api/player/command")
    assert resp.status_code == 200
    assert resp.json() == {}
