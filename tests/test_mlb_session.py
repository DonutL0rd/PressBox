"""Tests for MLBSession — Okta auth, token refresh, retry logic, heartbeat."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tv_automator.providers.mlb_session import MLBSession


# ── Response factories ────────────────────────────────────────────

def okta_response(expires_in: int = 3600) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "access_token": "access_tok",
        "refresh_token": "refresh_tok",
        "expires_in": expires_in,
    }
    resp.raise_for_status = MagicMock()
    return resp


def init_session_response(device_id: str = "dev_123") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "initSession": {
                "deviceId": device_id,
                "sessionId": "sess_456",
                "entitlements": [{"code": "MLB_TV_PRO"}],
            }
        }
    }
    resp.raise_for_status = MagicMock()
    return resp


def playback_session_response(url: str = "https://hls.example.com/master.m3u8") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "initPlaybackSession": {
                "playbackSessionId": "pb_sess_789",
                "playback": {
                    "url": url,
                    "token": "stream_token",
                    "expiration": "2024-04-01T23:00:00Z",
                    "cdn": "akamai",
                },
                "heartbeatInfo": {
                    "url": "https://heartbeat.example.com/hb",
                    "interval": 30,
                },
            }
        }
    }
    resp.raise_for_status = MagicMock()
    return resp


def content_search_response(media_id: str = "media_abc") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "data": {
            "contentSearch": {
                "content": [
                    {
                        "contentId": "content_1",
                        "mediaId": media_id,
                        "contentType": "GAME",
                        "feedType": "HOME",
                        "mediaState": {"state": "MEDIA_ON", "mediaType": "VIDEO"},
                    }
                ]
            }
        }
    }
    resp.raise_for_status = MagicMock()
    return resp


def error_response(status_code: int = 401, text: str = "Unauthorized") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error", request=MagicMock(), response=resp
    )
    return resp


# ── Login ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success():
    session = MLBSession()
    session._client.request = AsyncMock(side_effect=[
        okta_response(),
        init_session_response(),
    ])
    result = await session.login("user@test.com", "pass")

    assert result is True
    assert session.is_authenticated
    assert session._device_id == "dev_123"
    assert session._session_id == "sess_456"
    assert session._username == "user@test.com"
    assert session._password == "pass"


@pytest.mark.asyncio
async def test_login_does_not_store_credentials_on_failure():
    session = MLBSession()
    session._client.request = AsyncMock(return_value=error_response(401))

    result = await session.login("user@test.com", "wrongpass")

    assert result is False
    assert session._username is None
    assert session._password is None
    assert not session.is_authenticated


@pytest.mark.asyncio
async def test_login_http_401_returns_false():
    session = MLBSession()
    session._client.request = AsyncMock(return_value=error_response(401))

    result = await session.login("user@test.com", "pass")
    assert result is False


@pytest.mark.asyncio
async def test_login_network_error_returns_false():
    session = MLBSession()
    session._client.request = AsyncMock(side_effect=httpx.NetworkError("connection refused"))

    result = await session.login("user@test.com", "pass")
    assert result is False


@pytest.mark.asyncio
async def test_login_graphql_error_returns_false():
    """If initSession GraphQL returns errors, login should fail."""
    graphql_err = MagicMock(spec=httpx.Response)
    graphql_err.status_code = 200
    graphql_err.json.return_value = {"errors": [{"message": "Not authorized"}]}
    graphql_err.raise_for_status = MagicMock()

    session = MLBSession()
    session._client.request = AsyncMock(side_effect=[
        okta_response(),
        graphql_err,
    ])
    result = await session.login("user@test.com", "pass")
    assert result is False


# ── is_authenticated ──────────────────────────────────────────────

def test_is_authenticated_false_with_no_token():
    session = MLBSession()
    assert not session.is_authenticated


def test_is_authenticated_false_with_expired_token():
    session = MLBSession()
    session._access_token = "tok"
    session._token_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert not session.is_authenticated


def test_is_authenticated_true_with_valid_token():
    session = MLBSession()
    session._access_token = "tok"
    session._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    assert session.is_authenticated


def test_is_authenticated_false_near_expiry_buffer():
    """Token within 60s of expiry is considered expired."""
    session = MLBSession()
    session._access_token = "tok"
    session._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert not session.is_authenticated


# ── ensure_authenticated ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_authenticated_returns_true_when_already_valid():
    session = MLBSession()
    session._access_token = "tok"
    session._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    result = await session.ensure_authenticated()
    assert result is True


@pytest.mark.asyncio
async def test_ensure_authenticated_uses_refresh_token():
    session = MLBSession()
    session._access_token = "old"
    session._token_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    session._refresh_token = "ref_tok"

    with patch("asyncio.sleep"):
        session._client.request = AsyncMock(side_effect=[
            okta_response(),
            init_session_response(),
        ])
        result = await session.ensure_authenticated()

    assert result is True
    assert session.is_authenticated


@pytest.mark.asyncio
async def test_ensure_authenticated_falls_back_to_password_when_refresh_fails():
    session = MLBSession()
    session._access_token = "old"
    session._token_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    session._refresh_token = "stale_ref"
    session._username = "user@test.com"
    session._password = "pw"

    with patch("asyncio.sleep"):
        session._client.request = AsyncMock(side_effect=[
            error_response(400),          # refresh fails
            okta_response(),              # password login
            init_session_response(),      # init session
        ])
        result = await session.ensure_authenticated()

    assert result is True


@pytest.mark.asyncio
async def test_ensure_authenticated_returns_false_with_no_credentials():
    session = MLBSession()
    session._token_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    # No refresh token, no username/password

    result = await session.ensure_authenticated()
    assert result is False


@pytest.mark.asyncio
async def test_refresh_invalidates_stale_refresh_token_on_failure():
    session = MLBSession()
    session._access_token = "old"
    session._token_expiry = datetime(2020, 1, 1, tzinfo=timezone.utc)
    session._refresh_token = "stale"

    with patch("asyncio.sleep"):
        session._client.request = AsyncMock(return_value=error_response(400))
        await session._refresh_access_token()

    assert session._refresh_token is None


# ── send_heartbeat ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_heartbeat_success():
    session = MLBSession()
    session._access_token = "tok"
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    session._client.request = AsyncMock(return_value=resp)

    result = await session.send_heartbeat("https://hb.example.com")
    assert result is True


@pytest.mark.asyncio
async def test_send_heartbeat_returns_false_on_4xx():
    session = MLBSession()
    session._access_token = "tok"
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 403
    session._client.request = AsyncMock(return_value=resp)

    result = await session.send_heartbeat("https://hb.example.com")
    assert result is False


@pytest.mark.asyncio
async def test_send_heartbeat_returns_false_on_network_error():
    session = MLBSession()
    session._access_token = "tok"
    session._client.request = AsyncMock(side_effect=httpx.NetworkError("down"))

    result = await session.send_heartbeat("https://hb.example.com")
    assert result is False


# ── _request retry logic ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_retries_on_5xx_then_succeeds():
    session = MLBSession()
    ok = MagicMock(spec=httpx.Response)
    ok.status_code = 200
    err = MagicMock(spec=httpx.Response)
    err.status_code = 503

    with patch("asyncio.sleep"):
        session._client.request = AsyncMock(side_effect=[err, ok])
        result = await session._request("GET", "https://example.com", retries=1)

    assert result.status_code == 200
    assert session._client.request.call_count == 2


@pytest.mark.asyncio
async def test_request_does_not_retry_on_4xx():
    session = MLBSession()
    err = MagicMock(spec=httpx.Response)
    err.status_code = 404
    session._client.request = AsyncMock(return_value=err)

    result = await session._request("GET", "https://example.com", retries=3)

    assert result.status_code == 404
    assert session._client.request.call_count == 1


@pytest.mark.asyncio
async def test_request_retries_on_network_error_then_succeeds():
    session = MLBSession()
    ok = MagicMock(spec=httpx.Response)
    ok.status_code = 200

    with patch("asyncio.sleep"):
        session._client.request = AsyncMock(side_effect=[httpx.NetworkError("down"), ok])
        result = await session._request("GET", "https://example.com", retries=1)

    assert result.status_code == 200


@pytest.mark.asyncio
async def test_request_raises_after_exhausting_retries():
    session = MLBSession()

    with patch("asyncio.sleep"):
        session._client.request = AsyncMock(side_effect=httpx.NetworkError("down"))
        with pytest.raises(httpx.NetworkError):
            await session._request("GET", "https://example.com", retries=1)

    assert session._client.request.call_count == 2  # initial + 1 retry


@pytest.mark.asyncio
async def test_request_returns_last_5xx_when_retries_exhausted():
    session = MLBSession()
    err = MagicMock(spec=httpx.Response)
    err.status_code = 503

    with patch("asyncio.sleep"):
        session._client.request = AsyncMock(return_value=err)
        result = await session._request("GET", "https://example.com", retries=2)

    assert result.status_code == 503
    assert session._client.request.call_count == 3  # initial + 2 retries


# ── GraphQL error handling ────────────────────────────────────────

@pytest.mark.asyncio
async def test_graphql_error_in_response_raises_runtime_error():
    session = MLBSession()
    session._access_token = "tok"

    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {"errors": [{"message": "Not authorized"}]}
    resp.raise_for_status = MagicMock()
    session._client.request = AsyncMock(return_value=resp)

    with pytest.raises(RuntimeError, match="GraphQL error"):
        await session._graphql("someOp", "query {}", {})


# ── get_stream_info ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_stream_info_success():
    session = MLBSession()
    session._access_token = "tok"
    session._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    session._device_id = "dev_123"
    session._session_id = "sess_456"

    session._client.request = AsyncMock(side_effect=[
        content_search_response("media_abc"),
        playback_session_response("https://hls.example.com/master.m3u8"),
    ])

    info = await session.get_stream_info("12345", "HOME")

    assert info is not None
    assert info.url == "https://hls.example.com/master.m3u8"
    assert info.heartbeat_url == "https://heartbeat.example.com/hb"
    assert info.heartbeat_interval == 30


@pytest.mark.asyncio
async def test_get_stream_info_returns_none_when_not_authenticated():
    session = MLBSession()
    # No token
    result = await session.get_stream_info("12345", "HOME")
    assert result is None


@pytest.mark.asyncio
async def test_get_stream_info_returns_none_when_no_media_found():
    session = MLBSession()
    session._access_token = "tok"
    session._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    session._device_id = "dev_123"
    session._session_id = "sess_456"

    empty_resp = MagicMock(spec=httpx.Response)
    empty_resp.status_code = 200
    empty_resp.json.return_value = {"data": {"contentSearch": {"content": []}}}
    empty_resp.raise_for_status = MagicMock()
    session._client.request = AsyncMock(return_value=empty_resp)

    result = await session.get_stream_info("12345", "HOME")
    assert result is None
