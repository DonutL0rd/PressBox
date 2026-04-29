# Changelog

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
