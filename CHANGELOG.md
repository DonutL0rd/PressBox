# Changelog

## [0.4.1] — 2026-04-29

### Changed
- **Unified configuration** — eliminated the two-track config system (YAML file + UI settings). All runtime/behavioral settings are now managed exclusively through the web UI and persisted to `$DATA_DIR/settings.json`. Infrastructure settings (display resolution, browser binary, data path) go in `docker-compose.yml` or `.env`
- **`docker-compose.yml`** — added commented optional env var block documenting `RESOLUTION`, `CHROME_PATH`, and credential overrides (`MLB_USERNAME/PASSWORD`, `NAVIDROME_URL/USERNAME/PASSWORD`)
- **`.env.example`** — updated to reflect all supported env vars with inline comments; removed references to the config file
- **`BrowserController`** — no longer accepts a `Config` argument; reads `RESOLUTION` and `CHROME_PATH` from env directly; Chrome flags hardcoded as module-level `_CHROME_FLAGS` constant
- **`GameScheduler`** — now accepts `AppSettings` instead of `Config`

### Removed
- `config/default.yaml` — infrastructure settings migrated to docker-compose env vars; runtime settings migrated to the web UI
- `src/tv_automator/config.py` — replaced by `src/tv_automator/settings.py`

### New files
- `src/tv_automator/settings.py` — `AppSettings` class; loads/saves flat JSON at `$DATA_DIR/settings.json` with typed convenience properties and env-var credential overrides

## [0.4.0] — 2026-04-28

### Added
- **Ambient Screensaver** — complete rewrite of `screensaver.html` from a single-game MLB dashboard to a dual-mode ambient display. Shows the full day's MLB schedule as a carousel (8-second crossfade between game cards) with team abbreviations, scores, inning state, venue, broadcast info, and probable pitchers. When music is playing, the layout splits: album art + track metadata on the left, schedule on the right, with a blurred album-glow background (`filter: blur(120px) saturate(1.8)`)
- **Screensaver Schedule Scale setting** — `screensaver_schedule_scale` (50–200%, default 100) applies `transform: scale()` to the schedule section for different display sizes. Configurable in Settings UI and pushed live via WebSocket
- **Pitch Tracker enhancements** — dynamic SVG coordinate mapping with per-batter `zone_top`/`zone_bot`, pitch color-coding by outcome (green in-play, orange strikes/fouls, blue balls), latest pitch highlighted with larger radius and speed label, comprehensive hash-based change detection instead of count-based
- **Batter Intel Card** — flashes batter stats (hits, RBIs, HR, walks) for 8 seconds when a new batter steps up. Controlled by `batter_intel_enabled` setting
- **Between-Innings Break Overlay** — displays game score, due-up batters, and pitcher summary (IP, H, R, ER, BB, K) during breaks. Controlled by `between_innings_enabled` setting
- **Overlay Delay setting** — `overlay_delay` (0–15 seconds, default 2) delays overlay updates to sync with TV broadcast delay
- **Stream Reconnection System** — `_do_reconnect()` with automatic retry (up to 3 attempts, 30-second delays), fallback to condensed game, and structured error/recovery broadcasts (`stream_error`, `stream_recovered`, `stream_dead`)
- **Stream Controls in Dashboard** — quality selector (auto + HLS resolution levels), captions toggle, pitch tracker toggle, batter intel toggle, innings break toggle, overlay delay slider, tracker size dropdown, TV power on/off via CEC
- **YouTube Playback Controls** — inline controls with play/pause, seek bar with time display, playback speed selector (0.5x–2x), captions toggle, and stop button
- **Music Transport Bar** — persistent bar in Music view with shuffle/prev/play-pause/next/repeat controls, progress bar with seek, album art thumbnail, track title/artist, like/unlike, and stop button
- **Stream Alert System** — centralized `showAlert(message, level)` via TvAutomator context, replaces per-component toast notifications. WebSocket `error`/`info` message types with deduplication via ID counter
- **Music API endpoints** — `GET /api/music/starred` (liked songs), `POST /api/music/star` (star/unstar with `{ id, action }`)
- **YouTube API endpoints** — `GET /api/youtube/state` (playback state), `POST /api/youtube/command` (play, pause, seek, speed, cc)
- **Shared HTTP client** — `httpx.AsyncClient` created at startup with connection pooling (10 max connections, 10s timeout), reused for pitch and score fetches, properly closed on shutdown
- **Music concurrency lock** — `asyncio.Lock` protecting `_stop_music_internal()` for thread-safe state management

### Changed
- **Design System** — replaced warm brown/espresso palette with cooler deep grays and teal accent. `#0e0603` → `#050505`, `#f0e0c8` → `#e0e0e0`, `#c39460` → `#1a9a8a`. Inter font family with explicit weight variants (200–700). Longer 2-second lazy transitions
- **HLS Buffer Tuning** — `maxBufferLength` 30s → 60s, `maxMaxBufferLength` 120s → 600s, `liveSyncDurationCount` 3 → 5, `liveMaxLatencyDurationCount` 6 → 10. Captions disabled by default on manifest parse
- **Pitch Tracker polling** — initial poll interval reduced from 2000ms to 500ms for faster batter intel appearance
- **Settings UI labels** — "Strike Zone Overlay" → "Pitch Tracker", "Strike Zone Size" → "Tracker Size"
- **Music status broadcasting** — deduplication via `playing|paused|song|position` comparison, skips redundant position updates
- **Game detail polling** — live game poll interval 20s → 30s, string comparison of JSON responses to skip identical fetches
- **Status broadcast** — now includes `youtube_video_id` field for frontend YouTube management
- **Heartbeat monitoring** — captures `_stream_info` reference at loop start to prevent race conditions, broadcasts structured error on failure
- **Timezone handling** — `GET /api/games` and game scheduler `refresh()` now use `datetime.now(ZoneInfo("America/Los_Angeles"))` instead of UTC `datetime.now()`, fixing wrong-day schedule fetch when container clock is UTC

### Fixed
- **Pitch tracker visibility** — properly responds to `strike_zone_enabled` setting toggles, removed stale `currentBatterId` tracking that caused phantom batter cards
- **Unnecessary re-renders** — `shallowEqual()` comparisons prevent setState when WebSocket data is identical, `React.memo()` on `GameListItem` and `VideoCard`, `useMemo()` for dashboard counts, `useCallback()` for event handlers
- **Fallback polling** — now only fires when WebSocket is disconnected (via `connectedRef`), previously fired every 60s regardless of connection state
- **Player command handling** — accepts both `command` and legacy `action` fields for backward compatibility, caption commands handle both HLS subtitles and native text tracks
- **Wrong-day schedule on screensaver** — container UTC time caused `/api/games` to return tomorrow's schedule after ~5 PM Pacific. Fixed by using Pacific timezone for date resolution in both `app.py` and `game_scheduler.py`

### Removed
- `src/tv_automator/web/frontend/src/views/NowPlaying.tsx` — full-screen now-playing view (328 lines), functionality moved inline to Dashboard/Music/YouTube views
- `src/tv_automator/web/frontend/src/views/NowPlaying.css` — 297 lines of styling
- `src/tv_automator/web/frontend/src/components/TopBar.tsx` — top bar component, replaced by embedded controls in each view
- `src/tv_automator/web/frontend/src/assets/hero.png`, `react.svg`, `vite.svg` — unused assets
- **`dvd_bounce` setting** — removed from backend, Settings UI, and screensaver

### New files
- `src/tv_automator/web/frontend/src/views/YouTube.css` — styles for YouTube view with inline playback controls

## [0.3.0] - 2026-04-13

### Added
- **React Frontend** — complete rewrite of the web UI using Vite, React, and TypeScript into a single-page application (SPA)
- **Music Library UI** — browser and playback control UI with Subsonic/Navidrome integration (`Music.tsx`), including queue management, volume control, and album tracklist drill-down UI
- **Modernized Dashboard UI** — improved game list, detail panel, persistent now-playing bar, and home/away feed selection integration (`Dashboard.tsx`)
- **YouTube Interface** — purely mobile-focused responsive list layout, sticky URL bar, and enhanced video cards with duration/progress tracking badges (`YouTube.tsx`)

### Changed
- **Web Interface Architecture** — replaced server-rendered legacy HTML dashboard templates with a standalone React SPA frontend
- **Music playback UX** — optimized music player UI with optimistic UI updates, debounced volume control, and split polling intervals for better performance without full-page reloads
- **Docker build** — updated `Dockerfile` to support serving the new frontend application

### Removed
- `src/tv_automator/web/templates/dashboard.html` — legacy template entirely replaced by the `src/tv_automator/web/frontend/` app

### New files
- `src/tv_automator/web/frontend/` — full Vite-based React project directory

## [0.2.0] - 2026-03-30

### Added
- **Web dashboard** at `http://<server-ip>:5000/` — replaces the SSH-based TUI
  - Dark-themed responsive card layout showing today's games
  - Home/Away feed selection buttons per game
  - Live score updates (auto-refresh every 30 seconds)
  - Date navigation (previous/next day)
  - Now-playing indicator and stop button
  - Auth status badge
- **API-based MLB.TV authentication** via Okta resource owner password grant
  - No browser login required — credentials from `.env` are used automatically
  - Tokens auto-refresh on expiry
  - Uses the same Okta endpoint as official MLB apps (`ids.mlb.com`)
- **HLS stream playback** via hls.js
  - Stream URLs fetched from MLB media gateway GraphQL API
  - Chrome navigates to a local player page — no MLB.TV web UI involved
  - Full-screen, zero-chrome video playback on the TV
- **`mlb_session.py`** — new module handling all MLB.TV API interactions:
  - Okta password grant authentication
  - GraphQL `initSession` for device/session registration
  - GraphQL `contentSearch` for mapping game IDs to media IDs
  - GraphQL `initPlaybackSession` for HLS stream URL retrieval
- **Feed selection** — choose between home and away broadcast feeds
- **Xvfb fallback** — container starts a virtual framebuffer if no X display is available
- **FastAPI backend** with endpoints:
  - `GET /` — dashboard
  - `GET /api/games` — schedule data
  - `POST /api/play/{game_id}` — start playback (with `feed` param)
  - `POST /api/stop` — stop playback
  - `GET /api/status` — current state
  - `GET /player` — HLS video player page
  - `GET /api/stream` — current stream URL (used by player)

### Changed
- **Entry point** now starts uvicorn on port 5000 instead of a Textual TUI
- **BrowserController** simplified — just `navigate(url)` and `stop_playback()`; no more provider-specific login/cookie management
- **MLBProvider** stripped to schedule-only — all auth and stream logic moved to `MLBSession`
- **StreamingProvider base class** simplified — removed `login()`, `navigate_to_game()`, `is_authenticated()` abstract methods
- **Docker image** no longer includes nginx or openssh-server
- **`pyproject.toml`** — replaced `textual` and `rich` with `fastapi`, `uvicorn`, and `httpx`

### Removed
- **TUI** (`tui/` directory) — replaced by web dashboard
- **Playwright-based login** — replaced by direct Okta API auth
- **SSH access** — no longer needed; dashboard is accessible from any browser
- **Cookie-based session management** — tokens are managed in-memory by `MLBSession`
- **nginx** — uvicorn serves directly on port 5000

## [0.1.0] - 2026-03-29

### Added
- Initial project structure
- Textual-based TUI with game schedule display
- MLB Stats API integration for schedule data
- Playwright + Chrome browser automation for MLB.TV playback
- Docker container with X11 passthrough for HDMI output
- Openbox window manager for kiosk mode
- Cookie persistence for MLB.TV sessions
- Game scheduler with auto-start support for favorite teams
- SSH server for remote TUI access
- systemd service for persistent xhost grants
- Helper scripts for X11 display diagnosis and setup
