# PressBox

Self-hosted sports streaming appliance. Runs in Docker on an Ubuntu server connected to a TV via HDMI. Control what's playing from any browser on your network through a web dashboard.

## What It Does

1. **Start the Docker container** on your server
2. **Open the dashboard** at `http://<server-ip>:5000/` from any device on your network
3. **Browse today's games** and click Home or Away to pick a feed
4. **The game plays on your TV** via full-screen HLS streaming in Chrome

Authentication is handled entirely via API (Okta password grant) — no browser login required. Provide your MLB.TV credentials in a `.env` file and the system logs in automatically on startup.

You can also use PressBox to:
- Cast **YouTube** videos directly to the TV, with watch history and suggested channels
- Stream music from your **Navidrome / Subsonic** server using an integrated player with queue management
- Control TV power via **HDMI-CEC**
- Show a **screensaver** (DVD-style bounce) when nothing is playing

## Quick Start

### Prerequisites

- Ubuntu server with HDMI connected to a TV
- Docker & Docker Compose
- A graphical session on the server (GDM, LightDM, or bare Xorg)
- An MLB.TV subscription

### 1. Clone & configure

```bash
git clone <repo-url> press_box
cd press_box
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

Go to `http://<server-ip>:5000/` in any browser. You'll see today's MLB schedule. Click **Home** or **Away** on any live game to start streaming it on the TV.

## How It Works

### Authentication

PressBox authenticates with MLB.TV via Okta's resource owner password grant — the same API that official MLB apps use internally. No browser-based login, no CAPTCHAs, no fragile form-filling.

On startup the system:
1. POSTs your credentials to `ids.mlb.com` and receives an access token
2. Initializes a GraphQL media session at `media-gateway.mlb.com`
3. Tokens auto-refresh when they expire

### Playback

When you click a game:
1. The backend queries the MLB media gateway for the game's HLS stream URL
2. Chrome (running on the server's display) navigates to a local player page
3. The player uses [hls.js](https://github.com/video-dev/hls.js/) to decode and play the adaptive HLS stream
4. The video appears full-screen on the TV

This bypasses the MLB.TV web player entirely. No DRM issues, no ad overlays, no UI chrome — just the video feed.

Chrome is launched with GPU hardware acceleration flags (`--enable-gpu-rasterization`, `--enable-zero-copy`, `--enable-features=VaapiVideoDecoder`) to reduce CPU pressure. It is automatically recycled every 8 hours of idle to prevent memory drift.

If a stream drops, the player retries up to 3 times (with 30-second delays) before falling back to the condensed game feed.

### Real-time Updates

The dashboard connects to a persistent WebSocket (`/ws`) that the server uses to push live state. Updates include: game list, current playback status, music state, volume, queue, autoplay, and alert messages. No polling.

### Schedule Data

Game schedules come from the public [MLB Stats API](https://github.com/toddrob99/MLB-StatsAPI) (`statsapi` Python package). The scheduler polls every 60 seconds for live score updates.

## Architecture

```
                Browser (laptop/phone)
                         |
                    http://:5000
                         |
+------------------------+-----------------------------------------+
|  Docker Container      |                                         |
|                        |                                         |
|  +---------------------v---------------------------------------+ |
|  |  FastAPI + uvicorn (port 5000)                             | |
|  |                                                            | |
|  |  GET  /           -> React SPA (MLB, YouTube, Music,       | |
|  |                       Settings views)                      | |
|  |  WS   /ws         -> Real-time state push                  | |
|  |  GET  /api/games  -> Schedule from Stats API               | |
|  |  POST /api/play   -> Resolve stream URL -> navigate        | |
|  |  POST /api/stop   -> Stop playback                         | |
|  |  POST /api/youtube -> Cast YouTube video                   | |
|  |  GET  /api/music/* -> Navidrome/Subsonic proxy             | |
|  |  GET  /api/pitches -> Live pitch data                      | |
|  |  POST /api/volume  -> System volume control                | |
|  |  POST /api/cec/*   -> HDMI-CEC commands                    | |
|  +------+------------------------------------+-----------------+ |
|         |                                    |                   |
|  +------v-----------+    +-----------------v-----------------+  |
|  | MLBSession       |    | BrowserController                 |  |
|  | (Okta auth +     |    | (Playwright + Chrome)             |  |
|  |  GraphQL)        |    +----------------+------------------+  |
|  +------------------+                     |                     |
|                                       X11 Socket                |
+-------------------------------------------------------+---------+
                                                        |
                                                   HDMI Output
                                                        |
                                                   +----v----+
                                                   |   TV    |
                                                   +---------+
```

## Features

### MLB Dashboard

- Today's game schedule with live scores and status
- Home/Away feed selection per game
- Live pitch tracker with SVG coordinate mapping and batter/pitcher stats
- Inline playback controls (overlay toggle, stop, reconnect)
- Favorite teams with optional auto-start when they go live
- Date navigation (previous/next day)

### YouTube

- Cast any YouTube URL to the TV
- Watch history (newest-first, deletable)
- Suggested channels list
- Progress tracking badges on video cards

### Music (Navidrome / Subsonic)

- Browse artists, albums, playlists, and radio stations
- Queue management with append/remove
- Volume control with audio sink selection
- Star/unstar tracks
- Optimistic UI updates with debounced volume

### Settings

- MLB.TV credentials update without restart
- Navidrome server URL and credentials
- Favorite teams configuration
- Autoplay queue management
- Screensaver toggle (DVD-style bounce animation)
- HDMI-CEC status and control

## Configuration

### Environment variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `MLB_USERNAME` | Yes | MLB.TV account email |
| `MLB_PASSWORD` | Yes | MLB.TV account password |
| `DISPLAY` | No | X display (default: `:0`) |
| `DATA_DIR` | No | Persistent data path (default: `/data`) |
| `CHROME_PATH` | No | Chrome binary override |

### Config file (`config/default.yaml`)

```yaml
providers:
  mlb:
    favorite_teams: ["NYY", "LAD"]   # 3-letter team codes
    auto_start: false                 # Auto-play when favorites go live

scheduler:
  poll_interval: 60                   # Seconds between schedule refreshes
  pre_game_minutes: 5                 # Minutes before start to begin setup

display:
  resolution: "1920x1080"

browser:
  args:
    - "--kiosk"
    - "--autoplay-policy=no-user-gesture-required"

cec:
  enabled: false
  power_off_on_stop: true

navidrome:
  server_url: ""
  username: ""
  password: ""
```

## Project Structure

```
press_box/
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
│   ├── nginx.conf
│   └── openbox-config/rc.xml
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
│   │   ├── templates/                 # HLS player, YouTube, screensaver pages
│   │   ├── static/                    # hls.js bundle
│   │   └── frontend/                  # React SPA (Vite + TypeScript)
│   │       └── src/
│   │           ├── views/             # Dashboard, YouTube, Music, Settings
│   │           ├── components/        # Sidebar, NowPlayingBar
│   │           └── hooks/             # useTvAutomator (WebSocket state)
│   ├── providers/
│   │   ├── base.py                    # Provider interface
│   │   ├── mlb.py                     # MLB schedule (Stats API)
│   │   └── mlb_session.py             # MLB auth + streams (Okta + GraphQL)
│   ├── automator/
│   │   ├── browser_control.py         # Chrome window management
│   │   └── cec_control.py             # HDMI-CEC via cec-client
│   └── scheduler/
│       └── game_scheduler.py          # Background schedule polling
├── config/default.yaml
├── .env.example
└── pyproject.toml
```

## Roadmap

- [x] Phase 1: MLB game playback with web dashboard
- [x] Phase 2: API-based auth (Okta), HLS streaming, home/away feed selection
- [x] Phase 3: React SPA frontend, music player, YouTube casting, WebSocket state
- [x] Phase 4: Favorite teams, autoplay queue, CEC control, pitch tracker, inline controls
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

Frontend dev server (with hot reload):

```bash
cd src/tv_automator/web/frontend
npm install
npm run dev
```

## License

MIT
