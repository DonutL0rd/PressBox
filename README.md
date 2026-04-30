# PressBox

Self-hosted streaming appliance for your TV. Runs in Docker on an Ubuntu server connected to a TV via HDMI. Control everything from a browser on your local network.

## What It Does

1. **Start the Docker container** on your server
2. **Open the dashboard** at `http://<server-ip>:5000/` from any device
3. **Watch MLB games** — live HLS streams with home/away feed selection, condensed game replays, pitch tracker, and batter intel overlays
4. **Play music** — browse and queue from Navidrome/Subsonic; audio plays server-side via mpv + PulseAudio
5. **Watch YouTube** — paste a URL or browse suggested channels; watch history with position resume
6. **Everything plays on your TV** — video via Chrome + HLS, music via the server's audio output

When idle, an ambient screensaver cycles through the day's MLB schedule with scores, innings, venue, and probable pitchers. When music is playing, the layout splits: album art and track metadata on the left, schedule carousel on the right.

Authentication with MLB.TV is handled entirely via API (Okta password grant) — no browser login required.

## Quick Start

### Prerequisites

- Ubuntu server with HDMI connected to a TV
- Docker & Docker Compose
- A graphical session on the server (GDM, LightDM, or bare Xorg)
- An MLB.TV subscription

### 1. Clone & configure

```bash
git clone <repo-url> TV-Automator
cd TV-Automator
cp .env.example .env
```

Edit `.env` and add your MLB.TV credentials:

```
MLB_USERNAME=you@example.com
MLB_PASSWORD=yourpassword
```

### 2. Grant Docker access to the display

The container needs permission to draw on the host's X display.

```bash
# One-time setup
./scripts/setup-xhost.sh

# Make it permanent (survives reboots)
sudo cp systemd/tv-automator-xhost.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tv-automator-xhost.service
```

### 3. Start the container

```bash
cd docker
docker compose up -d
```

### 4. Open the dashboard

Go to `http://<server-ip>:5000/` in any browser. Click **Home** or **Away** on any live game to start streaming it on the TV.

## How It Works

### Authentication

TV-Automator authenticates with MLB.TV via Okta's resource owner password grant — the same API that official MLB apps use internally. No browser-based login, no CAPTCHAs, no fragile form-filling.

On startup the system:

1. POSTs your credentials to `ids.mlb.com` and receives an access token
2. Initializes a GraphQL media session at `media-gateway.mlb.com`
3. Tokens auto-refresh before expiry; a watchdog re-authenticates if they lapse

### MLB Playback

When you click a game:

1. The backend queries the MLB media gateway for the game's HLS stream URL
2. Chrome (running on the server's display) navigates to a local player page (`/player`)
3. The player uses hls.js via a server-side HLS proxy (`/hls/`) to bypass CORS restrictions
4. Video appears full-screen on the TV with optional pitch tracker and overlay data

Condensed game replays use the public MLB Stats API CDN — no auth required.

### Music

Music plays server-side via **mpv** + PulseAudio. The dashboard is a remote control — audio comes from the server's audio output, not the browser. Navidrome (Subsonic API) is the music source. Browse artists, albums, playlists, and internet radio; queue songs; control shuffle/repeat from any device on the network.

### YouTube

Paste any YouTube URL or browse recent videos from configured channels (fetched from public RSS feeds). Chrome navigates to a local TV-optimized page (`/tv/youtube`). Watch history and playback position are saved to disk so you can resume where you left off.

### Screensaver

When idle, Chrome displays an ambient screensaver (`/screensaver`) that rotates through the day's MLB schedule with 8-second crossfades between game cards. When music is playing, the layout splits to show album art alongside the schedule.

### Reliability

A background watchdog monitors browser health every 30 seconds and restarts Chrome if it crashes. Streams reconnect automatically on failure (up to 3 retries). Chrome recycles itself after 8 hours of idle to prevent memory leaks.

## Architecture

```
                Browser (laptop/phone)
                         │
                    http://:5000
                         │
┌────────────────────────┼──────────────────────────────┐
│  Docker Container      │                              │
│                        │                              │
│  ┌─────────────────────▼───────────────────────────┐  │
│  │  FastAPI + uvicorn (port 5000)                  │  │
│  │                                                 │  │
│  │  GET  /              → React SPA (Dashboard)    │  │
│  │  GET  /api/games     → MLB schedule             │  │
│  │  POST /api/play      → Fetch stream → navigate  │  │
│  │  POST /api/stop      → Stop playback            │  │
│  │  POST /api/youtube   → Play YouTube video       │  │
│  │  GET  /api/music/*   → Music library & control  │  │
│  │  POST /api/music/*   → Playback + queue control │  │
│  │  GET  /player        → HLS player + overlays    │  │
│  │  GET  /screensaver   → Ambient schedule display │  │
│  │  GET  /tv/youtube    → TV-side YouTube player   │  │
│  │  GET  /hls/*         → HLS proxy (CORS bypass)  │  │
│  │  WS   /ws            → Real-time state push     │  │
│  └──────┬──────────┬──────────┬─────────────────────┘  │
│         │          │          │                     │
│  ┌──────▼───────┐  │  ┌───────▼──────────────────┐ │
│  │ MLBSession   │  │  │ BrowserController        │ │
│  │ (Okta auth + │  │  │ (Playwright + Chrome)    │ │
│  │  GraphQL)    │  │  └──────────┬───────────────┘ │
│  └──────────────┘  │             │                 │
│                    │        X11 Socket              │
│  ┌─────────────────▼──┐    ┌────▼───┐             │
│  │ Navidrome Client   │    │  mpv   │             │
│  │ (Subsonic API)     │    │ + PA   │             │
│  └────────────────────┘    └────────┘             │
│                                                    │
│  ┌──────────────────┐     HDMI / Audio output      │
│  │  CECController   ├─────────────────────────┐   │
│  │ (TV power on/off)│                         │   │
│  └──────────────────┘                         │   │
└───────────────────────────────────────────────┼───┘
                                                │
                                           ┌────▼────┐
                                           │   TV    │
                                           └─────────┘
```

## Configuration

### Environment variables (`.env`)

| Variable             | Required | Description                                             |
| -------------------- | -------- | ------------------------------------------------------- |
| `MLB_USERNAME`       | Yes      | MLB.TV account email                                    |
| `MLB_PASSWORD`       | Yes      | MLB.TV account password                                 |
| `DISPLAY`            | No       | X display (default: `:0`)                               |
| `DATA_DIR`           | No       | Persistent data path (default: `/data`)                 |
| `CHROME_PATH`        | No       | Chrome binary override                                  |
| `NAVIDROME_URL`      | No       | Navidrome server URL (e.g. `http://192.168.1.100:4533`) |
| `NAVIDROME_USERNAME` | No       | Navidrome account username                              |

The Navidrome password and all other runtime settings are configured through the **Settings** view in the dashboard and saved to `config/user.yaml`.

### Config file (`config/default.yaml`)

```yaml
providers:
  mlb:
    favorite_teams: ["NYY", "LAD"] # 3-letter team codes
    auto_start: false # Auto-play when favorites go live
    default_feed: "HOME" # Default broadcast feed (HOME or AWAY)

scheduler:
  poll_interval: 60 # Seconds between schedule refreshes
  pre_game_minutes: 5 # Minutes before start to watch for auto-start

display:
  resolution: "1920x1080"
  fullscreen: true

cec:
  enabled: false # HDMI CEC — power TV on/off with playback
  power_off_on_stop: true

screensaver:
  schedule_scale: 100 # Schedule section zoom (50–200%)
  music_size: medium # Album art size when music plays (small/medium/large)

data_dir: "/data"
```

### Settings UI

All runtime settings are available in the **Settings** view without editing files:

- **MLB credentials** — saved and verified against the Okta API on submission
- **Navidrome credentials** — server URL, username, and password
- **Playback** — auto-start favorites, default broadcast feed
- **Overlays** — pitch tracker toggle and size, batter intel card, between-innings overlay, overlay delay (0–15s to sync with broadcast delay)
- **System** — HDMI CEC, schedule poll interval, screensaver schedule scale and music panel size
- **YouTube channels** — add/remove channels by channel ID for the suggested videos feed

## Project Structure

```
TV-Automator/
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── entrypoint.sh
├── scripts/
│   ├── diagnose-display.sh
│   └── setup-xhost.sh
├── systemd/
│   └── tv-automator-xhost.service
├── src/tv_automator/
│   ├── main.py                        # Entry point (uvicorn)
│   ├── config.py                      # Layered config (yaml + env)
│   ├── web/
│   │   ├── app.py                     # FastAPI routes + WebSocket hub
│   │   ├── templates/
│   │   │   ├── player.html            # HLS video player + pitch tracker overlay
│   │   │   ├── screensaver.html       # Ambient schedule + music display
│   │   │   └── youtube.html           # TV-optimized YouTube player page
│   │   └── frontend/                  # React SPA (Vite + TypeScript)
│   │       └── src/
│   │           ├── views/
│   │           │   ├── Dashboard.tsx   # Game list + stream controls
│   │           │   ├── Music.tsx       # Music library + transport bar
│   │           │   ├── YouTube.tsx     # Video browser + watch history
│   │           │   └── Settings.tsx    # Credentials, overlay, and display settings
│   │           ├── components/
│   │           │   ├── Sidebar.tsx     # Navigation sidebar
│   │           │   └── NowPlayingBar.tsx  # Persistent now-playing strip
│   │           └── hooks/
│   │               └── useTvAutomator.tsx  # Global state + WebSocket
│   ├── providers/
│   │   ├── base.py                    # Provider interface (Game, Team, GameStatus)
│   │   ├── mlb.py                     # MLB schedule (Stats API)
│   │   └── mlb_session.py             # MLB auth + streams (Okta + GraphQL)
│   ├── automator/
│   │   ├── browser_control.py         # Chrome window management (Playwright)
│   │   └── cec_control.py             # HDMI CEC — TV power on/off
│   └── scheduler/
│       └── game_scheduler.py          # Background schedule polling + auto-start
├── config/default.yaml
├── .env.example
└── pyproject.toml
```

## Roadmap

- [x] Phase 1: MLB game playback with web dashboard
- [x] Phase 2: API-based auth (Okta), HLS streaming, home/away feed selection
- [x] Phase 3: React SPA, music integration (Navidrome), YouTube playback
- [x] Phase 4: Ambient screensaver, pitch tracker, batter intel, between-innings overlays
- [ ] Phase 5: Multiview (picture-in-picture / split-screen)
- [ ] Phase 6: Additional providers (F1 TV, NBA, NHL, NFL)

## Development

```bash
# Local dev (without Docker — needs Chrome installed)
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env  # fill in credentials
python -m tv_automator.main
```

## License

MIT
