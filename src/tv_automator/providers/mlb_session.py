"""MLB.TV API session — handles Okta auth, stream URLs, and heartbeats."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

# Okta / MLB constants (from the Kodi plugin.video.mlbtv)
OKTA_TOKEN_URL = "https://ids.mlb.com/oauth2/aus1m088yK07noBfh356/v1/token"
OKTA_CLIENT_ID = "0oa3e1nutA1HLzAKG356"
GRAPHQL_URL = "https://media-gateway.mlb.com/graphql"

# Retry / resilience defaults
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 8]  # seconds between retries
TOKEN_EXPIRY_BUFFER = timedelta(seconds=60)  # refresh 60s before actual expiry

_INIT_SESSION_MUTATION = (
    "mutation initSession($device: InitSessionInput!, $clientType: ClientType!, "
    "$experience: ExperienceTypeInput) { initSession(device: $device, clientType: "
    "$clientType, experience: $experience) { deviceId sessionId entitlements { code } "
    "location { countryCode regionName zipCode latitude longitude } "
    "clientExperience features } }"
)

_CONTENT_SEARCH_QUERY = (
    "query contentSearch($query: String!, $limit: Int = 10, $skip: Int = 0) { "
    "contentSearch(query: $query, limit: $limit, skip: $skip) { total content { "
    "contentId mediaId contentType feedType callSign mediaState { state mediaType "
    "contentExperience } } } }"
)

_PLAYBACK_SESSION_MUTATION = (
    "mutation initPlaybackSession($adCapabilities: [AdExperienceType], "
    "$mediaId: String!, $deviceId: String!, $sessionId: String!, "
    "$quality: PlaybackQuality) { initPlaybackSession(adCapabilities: "
    "$adCapabilities, mediaId: $mediaId, deviceId: $deviceId, sessionId: "
    "$sessionId, quality: $quality) { playbackSessionId playback { url token "
    "expiration cdn } heartbeatInfo { url interval } } }"
)

_WEB_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class StreamInfo:
    """Stream URL plus heartbeat and expiry metadata."""
    url: str
    heartbeat_url: str | None = None
    heartbeat_interval: int = 120  # seconds
    expiration: datetime | None = None  # when the stream URL expires


class MLBSession:
    """Handles MLB.TV authentication and stream access via API."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: datetime | None = None
        self._device_id: str | None = None
        self._session_id: str | None = None
        self._username: str | None = None
        self._password: str | None = None

    # ── Public API ──────────────────────────────────────────────

    async def login(self, username: str, password: str) -> bool:
        """Authenticate with MLB.TV via Okta password grant."""
        self._username = username
        self._password = password

        try:
            resp = await self._request(
                "POST", OKTA_TOKEN_URL,
                data={
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                    "scope": "openid offline_access",
                    "client_id": OKTA_CLIENT_ID,
                },
                headers={"User-Agent": "okhttp/3.12.1"},
                retries=2,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            log.error("Okta login failed (HTTP %d): %s", e.response.status_code, e.response.text[:200])
            return False
        except Exception:
            log.exception("Okta login request failed")
            return False

        self._apply_token_response(data)
        log.info("Okta login successful (token expires in %ds)", data.get("expires_in", 0))

        try:
            await self._init_session()
            return True
        except Exception:
            log.exception("Failed to initialize MLB session after login")
            return False

    @property
    def is_authenticated(self) -> bool:
        return (
            self._access_token is not None
            and self._token_expiry is not None
            and datetime.now(timezone.utc) < (self._token_expiry - TOKEN_EXPIRY_BUFFER)
        )

    async def ensure_authenticated(self) -> bool:
        """Ensure we have a valid token. Tries: check → refresh → re-login."""
        if self.is_authenticated:
            return True

        # Try refresh token first (no password needed)
        if self._refresh_token:
            log.info("Access token expired — attempting refresh...")
            if await self._refresh_access_token():
                return True
            log.warning("Refresh token failed — falling back to password login")

        # Fall back to full re-login
        if self._username and self._password:
            log.info("Re-authenticating with password...")
            return await self.login(self._username, self._password)

        return False

    async def get_stream_info(self, game_id: str, feed_type: str = "HOME") -> StreamInfo | None:
        """Get an HLS stream URL + heartbeat info. Returns None on failure."""
        if not await self.ensure_authenticated():
            log.error("Cannot get stream — not authenticated")
            return None

        media_id = await self._get_media_id(game_id, feed_type)
        if not media_id:
            log.error("No media found for game %s (feed=%s)", game_id, feed_type)
            return None

        try:
            result = await self._graphql("initPlaybackSession", _PLAYBACK_SESSION_MUTATION, {
                "adCapabilities": ["GOOGLE_STANDALONE_AD_PODS"],
                "mediaId": media_id,
                "deviceId": self._device_id,
                "sessionId": self._session_id,
                "quality": "PLACEHOLDER",
            })
            playback = result["data"]["initPlaybackSession"]
            heartbeat = playback.get("heartbeatInfo") or {}
            pb = playback["playback"]
            expiry = None
            if pb.get("expiration"):
                try:
                    expiry = datetime.fromisoformat(pb["expiration"].replace("Z", "+00:00"))
                except Exception:
                    pass
            info = StreamInfo(
                url=pb["url"],
                heartbeat_url=heartbeat.get("url"),
                heartbeat_interval=heartbeat.get("interval", 120),
                expiration=expiry,
            )
            log.info("Got stream for game %s (feed=%s, heartbeat=%ds, expires=%s)",
                     game_id, feed_type, info.heartbeat_interval,
                     expiry.isoformat() if expiry else "unknown")
            return info
        except Exception:
            log.exception("Failed to get playback session for game %s", game_id)
            return None

    async def send_heartbeat(self, heartbeat_url: str) -> bool:
        """Send a heartbeat to keep the stream alive."""
        try:
            resp = await self._request(
                "POST", heartbeat_url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "User-Agent": _WEB_USER_AGENT,
                },
                retries=2,
            )
            ok = resp.status_code < 400
            if not ok:
                log.warning("Heartbeat returned HTTP %d", resp.status_code)
            return ok
        except Exception:
            log.warning("Heartbeat failed", exc_info=True)
            return False

    async def close(self) -> None:
        await self._client.aclose()

    # ── Token management ────────────────────────────────────────

    def _apply_token_response(self, data: dict) -> None:
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        expires_in = data.get("expires_in", 3600)
        self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    async def _refresh_access_token(self) -> bool:
        """Use the refresh token to get a new access token without re-sending credentials."""
        try:
            resp = await self._request(
                "POST", OKTA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": OKTA_CLIENT_ID,
                    "scope": "openid offline_access",
                },
                headers={"User-Agent": "okhttp/3.12.1"},
                retries=1,
            )
            resp.raise_for_status()
            data = resp.json()
            self._apply_token_response(data)
            log.info("Token refreshed (expires in %ds)", data.get("expires_in", 0))

            # Re-init session with the new token
            await self._init_session()
            return True
        except httpx.HTTPStatusError as e:
            log.warning("Token refresh failed (HTTP %d)", e.response.status_code)
            self._refresh_token = None  # invalidate stale refresh token
            return False
        except Exception:
            log.warning("Token refresh failed", exc_info=True)
            return False

    # ── GraphQL / API helpers ───────────────────────────────────

    async def _init_session(self) -> None:
        result = await self._graphql("initSession", _INIT_SESSION_MUTATION, {
            "device": {
                "appVersion": "7.8.2",
                "deviceFamily": "desktop",
                "knownDeviceId": self._device_id or "",
                "languagePreference": "ENGLISH",
                "manufacturer": "Google Inc.",
                "model": "",
                "os": "linux",
                "osVersion": "1.0",
            },
            "clientType": "WEB",
        })
        session = result["data"]["initSession"]
        self._device_id = session["deviceId"]
        self._session_id = session["sessionId"]
        entitlements = [e["code"] for e in session.get("entitlements", [])]
        log.info("MLB session ready (device=%s, entitlements=%s)",
                 self._device_id[:12] + "...", entitlements)

    async def _get_media_id(self, game_id: str, feed_type: str) -> str | None:
        try:
            result = await self._graphql("contentSearch", _CONTENT_SEARCH_QUERY, {
                "query": (
                    f'GamePk={game_id} AND ContentType="GAME"'
                    " RETURNING HomeTeamId, HomeTeamName, AwayTeamId,"
                    " AwayTeamName, Date, MediaType, ContentExperience,"
                    " MediaState, PartnerCallLetters"
                ),
                "limit": 16,
            })
        except Exception:
            log.exception("Content search failed for game %s", game_id)
            return None

        contents = result.get("data", {}).get("contentSearch", {}).get("content", [])
        if not contents:
            return None

        # Prefer VIDEO feeds over AUDIO feeds
        for item in contents:
            state = item.get("mediaState") or {}
            if (item.get("feedType", "").upper() == feed_type.upper()
                    and state.get("mediaType", "").upper() == "VIDEO"):
                return item["mediaId"]

        # Fall back to any matching feed type
        for item in contents:
            if item.get("feedType", "").upper() == feed_type.upper():
                return item["mediaId"]
        return contents[0].get("mediaId")

    async def _graphql(self, operation: str, query: str, variables: dict) -> dict:
        resp = await self._request(
            "POST", GRAPHQL_URL,
            json={"operationName": operation, "query": query, "variables": variables},
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "User-Agent": _WEB_USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            log.error("GraphQL errors for %s: %s", operation, data["errors"])
            raise RuntimeError(f"GraphQL error in {operation}: {data['errors']}")
        return data

    # ── HTTP with retry ─────────────────────────────────────────

    async def _request(
        self, method: str, url: str, *, retries: int = MAX_RETRIES, **kwargs
    ) -> httpx.Response:
        """HTTP request with automatic retry on transient failures."""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = await self._client.request(method, url, **kwargs)
                # Retry on 5xx server errors
                if resp.status_code >= 500 and attempt < retries:
                    log.warning("%s %s returned %d — retry %d/%d",
                                method, url[:80], resp.status_code, attempt + 1, retries)
                    await asyncio.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                    continue
                return resp
            except (httpx.NetworkError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt < retries:
                    wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                    log.warning("%s %s failed (%s) — retry %d/%d in %ds",
                                method, url[:80], type(e).__name__, attempt + 1, retries, wait)
                    await asyncio.sleep(wait)
                    continue
                raise
        raise last_exc  # type: ignore[misc]
