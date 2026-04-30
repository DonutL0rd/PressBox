"""Tests for the player module — state management, play/reconnect logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import tv_automator.web.player as player_module
from tv_automator.providers.mlb_session import StreamInfo


# ── Fixtures ──────────────────────────────────────────────────────

def make_mock_ctx(browser_running=True, browser_navigate_ok=True, auth_ok=True):
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.ensure_authenticated = AsyncMock(return_value=auth_ok)
    ctx.session.get_stream_info = AsyncMock()
    ctx.session.send_heartbeat = AsyncMock(return_value=True)
    ctx.browser = MagicMock()
    ctx.browser.is_running = browser_running
    ctx.browser.navigate = AsyncMock(return_value=browser_navigate_ok)
    ctx.browser.start = AsyncMock()
    ctx.cec = MagicMock()
    ctx.cec.enabled = False
    ctx.broadcast = AsyncMock()
    ctx.broadcast_status = AsyncMock()
    ctx.play_lock = AsyncMock()
    ctx.play_lock.__aenter__ = AsyncMock(return_value=None)
    ctx.play_lock.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.fixture(autouse=True)
def reset_player_state():
    """Reset all player module globals before and after each test."""
    player_module.clear_player_state()
    player_module.stop_heartbeat()
    player_module.stop_expiry_timer()
    yield
    player_module.clear_player_state()
    player_module.stop_heartbeat()
    player_module.stop_expiry_timer()


# ── State accessors ───────────────────────────────────────────────

def test_initial_state_is_empty():
    assert player_module.get_now_playing_game_id() is None
    assert player_module.get_now_playing_feed() == "HOME"
    assert player_module.get_stream_info() is None
    assert not player_module.heartbeat_active()


def test_clear_player_state_resets_all_fields():
    player_module._now_playing_game_id = "123"
    player_module._now_playing_feed = "AWAY"
    player_module._stream_info = MagicMock()
    player_module._player_levels = [{"height": 1080}]
    player_module._player_command = {"action": "quality"}

    player_module.clear_player_state()

    assert player_module.get_now_playing_game_id() is None
    assert player_module.get_now_playing_feed() == "HOME"
    assert player_module.get_stream_info() is None
    assert player_module._player_levels == []
    assert player_module._player_command is None


def test_heartbeat_active_false_when_no_task():
    assert not player_module.heartbeat_active()


def test_set_and_get_browser_started_at():
    player_module.set_browser_started_at(99999.0)
    assert player_module.get_browser_started_at() == 99999.0


# ── do_play ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_play_sets_state_on_success():
    ctx = make_mock_ctx()
    player_module.init(ctx)

    stream = StreamInfo(
        url="https://hls.example.com/master.m3u8",
        heartbeat_url="https://hb.example.com",
        heartbeat_interval=30,
    )
    ctx.session.get_stream_info = AsyncMock(return_value=stream)

    await player_module.do_play("12345", "HOME")

    assert player_module.get_now_playing_game_id() == "12345"
    assert player_module.get_now_playing_feed() == "HOME"
    assert player_module.get_stream_info() is stream


@pytest.mark.asyncio
async def test_do_play_navigates_browser_to_player():
    ctx = make_mock_ctx()
    player_module.init(ctx)
    stream = StreamInfo(url="https://hls.example.com/master.m3u8")
    ctx.session.get_stream_info = AsyncMock(return_value=stream)

    await player_module.do_play("12345", "HOME")

    ctx.browser.navigate.assert_called_once()
    call_url = ctx.browser.navigate.call_args[0][0]
    assert "/player" in call_url


@pytest.mark.asyncio
async def test_do_play_broadcasts_status():
    ctx = make_mock_ctx()
    player_module.init(ctx)
    stream = StreamInfo(url="https://hls.example.com/master.m3u8")
    ctx.session.get_stream_info = AsyncMock(return_value=stream)

    await player_module.do_play("12345", "HOME")

    ctx.broadcast_status.assert_called_once()


@pytest.mark.asyncio
async def test_do_play_raises_401_when_not_authenticated():
    ctx = make_mock_ctx(auth_ok=False)
    player_module.init(ctx)

    with pytest.raises(HTTPException) as exc_info:
        await player_module.do_play("12345", "HOME")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_do_play_raises_502_when_no_stream_url():
    ctx = make_mock_ctx()
    ctx.session.get_stream_info = AsyncMock(return_value=None)
    player_module.init(ctx)

    with pytest.raises(HTTPException) as exc_info:
        await player_module.do_play("12345", "HOME")
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_do_play_raises_503_when_browser_navigate_fails():
    ctx = make_mock_ctx(browser_navigate_ok=False)
    stream = StreamInfo(url="https://hls.example.com/master.m3u8")
    ctx.session.get_stream_info = AsyncMock(return_value=stream)
    player_module.init(ctx)

    with pytest.raises(HTTPException) as exc_info:
        await player_module.do_play("12345", "HOME")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_do_play_powers_on_cec_when_enabled():
    ctx = make_mock_ctx()
    ctx.cec.enabled = True
    ctx.cec.power_on = AsyncMock()
    ctx.cec.set_active_source = AsyncMock()
    stream = StreamInfo(url="https://hls.example.com/master.m3u8")
    ctx.session.get_stream_info = AsyncMock(return_value=stream)
    player_module.init(ctx)

    await player_module.do_play("12345", "HOME")

    ctx.cec.power_on.assert_called_once()
    ctx.cec.set_active_source.assert_called_once()


@pytest.mark.asyncio
async def test_do_play_starts_heartbeat_when_heartbeat_url_present():
    ctx = make_mock_ctx()
    player_module.init(ctx)
    stream = StreamInfo(
        url="https://hls.example.com/master.m3u8",
        heartbeat_url="https://hb.example.com",
        heartbeat_interval=30,
    )
    ctx.session.get_stream_info = AsyncMock(return_value=stream)

    await player_module.do_play("12345", "HOME")

    assert player_module.heartbeat_active()


# ── do_reconnect ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_reconnect_returns_none_when_no_game_playing():
    ctx = make_mock_ctx()
    player_module.init(ctx)

    result = await player_module.do_reconnect()
    assert result is None


@pytest.mark.asyncio
async def test_do_reconnect_fetches_fresh_stream():
    ctx = make_mock_ctx()
    player_module.init(ctx)
    player_module._now_playing_game_id = "12345"
    player_module._now_playing_feed = "HOME"

    new_stream = StreamInfo(url="https://hls.example.com/new.m3u8")
    ctx.session.get_stream_info = AsyncMock(return_value=new_stream)

    result = await player_module.do_reconnect(schedule_retry=False)

    assert result is new_stream
    assert player_module.get_stream_info() is new_stream


@pytest.mark.asyncio
async def test_do_reconnect_navigates_browser():
    ctx = make_mock_ctx()
    player_module.init(ctx)
    player_module._now_playing_game_id = "12345"
    player_module._now_playing_feed = "HOME"

    new_stream = StreamInfo(url="https://hls.example.com/new.m3u8")
    ctx.session.get_stream_info = AsyncMock(return_value=new_stream)

    await player_module.do_reconnect(schedule_retry=False)

    ctx.browser.navigate.assert_called_once()


@pytest.mark.asyncio
async def test_do_reconnect_broadcasts_error_when_no_stream():
    ctx = make_mock_ctx()
    ctx.session.get_stream_info = AsyncMock(return_value=None)
    player_module.init(ctx)
    player_module._now_playing_game_id = "12345"
    player_module._now_playing_feed = "HOME"

    result = await player_module.do_reconnect(schedule_retry=False)

    assert result is None
    ctx.broadcast.assert_called_once()
    msg = ctx.broadcast.call_args[0][0]
    assert msg["type"] == "error"
    assert msg["code"] == "stream_error"


# ── do_play_condensed ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_do_play_condensed_raises_404_when_no_url():
    ctx = make_mock_ctx()
    player_module.init(ctx)

    with pytest.raises(HTTPException) as exc_info:
        # No real HTTP call — _get_condensed_url needs to be patched
        from unittest.mock import patch
        with patch.object(player_module, "_get_condensed_url", AsyncMock(return_value=None)):
            await player_module.do_play_condensed("12345")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_do_play_condensed_sets_direct_stream():
    ctx = make_mock_ctx()
    player_module.init(ctx)

    from unittest.mock import patch
    with patch.object(
        player_module, "_get_condensed_url",
        AsyncMock(return_value="https://cdn.example.com/condensed.mp4"),
    ):
        await player_module.do_play_condensed("12345")

    assert player_module.get_now_playing_game_id() == "12345"
    assert player_module.get_now_playing_feed() == "CONDENSED"
    stream = player_module.get_stream_info()
    assert stream is not None
    assert stream.direct is True
    assert stream.url == "https://cdn.example.com/condensed.mp4"
