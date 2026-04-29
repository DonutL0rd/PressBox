# TV-Automator

Self-hosted sports streaming appliance. Runs in Docker on an Ubuntu server connected to a TV via HDMI. Control what's playing from any browser on your network through a web dashboard.

## What It Does

1. **Start the Docker container** on your server
2. **Open the dashboard** at `http://<server-ip>:5000/` from any device
3. **Browse today's games**, play music, or queue up YouTube videos
4. **Everything plays on your TV** — MLB via HLS, music via Navidrome, YouTube via Chrome

When nothing is playing, an ambient screensaver shows the day's MLB schedule cycling through each game with scores, innings, and matchup details. If music is playing, the screensaver splits to show album art alongside the schedule.

Authentication is handled entirely via API (Okta password grant) — no browser login required. Provide your MLB.TV credentials in a `.env` file and the system logs in automatically on startup.

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

Go to `http://<server-ip>:5000/` in any browser. You'll see today's MLB schedule. Click **Home** or **Away** on any live game to start streaming it on the TV.

## How It Works

### Authentication

TV-Automator authenticates with MLB.TV via Okta's resource owner password grant — the same API that official MLB apps use internally. No browser-based login, no CAPTCHAs, no fragile form-filling.

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

This bypasses the MLB.TV web player entirely. No DRM issues, no ads overlay, no UI chrome — just the video feed.

### Schedule Data

Game schedules come from the public [MLB Stats API](https://github.com/toddrob99/MLB-StatsAPI) (`statsapi` Python package). The scheduler polls every 60 seconds for live score updates.

## Architecture

```
                Browser (laptop/phone)
                         │
                    http://:5000
                         │
┌────────────────────────┼─────────────────────────────┐
│  Docker Container      │                             │
│                        │                             │
│  ┌─────────────────────▼──────────────────────────┐  │
│  │  FastAPI + uvicorn (port 5000)                 │  │
│  │                                                │  │
│  │  GET  /            → React SPA Frontend        │  │
│  │  GET  /api/games   → Schedule from Stats API   │  │
│  │  POST /api/play    → Get stream URL → navigate │  │
│  │  POST /api/stop    → Stop playback             │  │
│  │  GET  /player      → HLS player + pitch tracker│  │
│  │  GET  /screensaver → Ambient schedule display  │  │
│  │  WS   /ws          → Real-time state push      │  │
│  └──────┬──────────┬─────────────┬────────────────┘  │
│         │          │             │                    │
│  ┌──────▼───────┐  │  ┌─────────▼───────────────┐   │
│  │ MLBSession   │  │  │ BrowserController       │   │
│  │ (Okta auth + │  │  │ (Playwright + Chrome)   │   │
│  │  GraphQL)    │  │  └──────────┬──────────────┘   │
│  └──────────────┘  │             │                   │
│                    │        X11 Socket               │
│  ┌─────────────────▼──┐         │                   │
│  │ Navidrome Client   │         │                   │
│  │ (Subsonic API)     │         │                   │
│  └────────────────────┘         │                   │
└─────────────────────────────────┼───────────────────┘
                                  │
                             HDMI Output
                                  │
                             ┌────▼────┐
                             │   TV    │
                             └─────────┘
```

## Configuration

### Environment variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `MLB_USERNAME` | Yes | MLB.TV account email |
| `MLB_PASSWORD` | Yes | MLB.TV account password |
| `DISPLAY` | No | X display (default: `:0`) |
| `DATA_DIR` | No | Persistent data path (default: `/data`) |
| `CHROME_PATH` | No | Chrome binary override |
| `NAVIDROME_URL` | No | Navidrome server URL (e.g. `http://192.168.1.100:4533`) |
| `NAVIDROME_USERNAME` | No | Navidrome account username |

### Config file (`config/default.yaml`)

```yaml
providers:
  mlb:
    favorite_teams: ["NYY", "LAD"]   # 3-letter team codes
    auto_start: false                 # Auto-play when favorites go live

scheduler:
  poll_interval: 60                   # Seconds between schedule refreshes

display:
  resolution: "1920x1080"

browser:
  args:
    - "--kiosk"
    - "--autoplay-policy=no-user-gesture-required"
```

## Project Structure

```
TV-Automator/
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
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
│   │   ├── templates/
│   │   │   ├── player.html            # HLS video player + pitch tracker overlay
│   │   │   └── screensaver.html       # Ambient schedule + music display
│   │   └── frontend/                  # React SPA (Vite + TypeScript)
│   │       └── src/
│   │           ├── views/
│   │           │   ├── Dashboard.tsx   # Game list + stream controls
│   │           │   ├── Music.tsx       # Music library + transport bar
│   │           │   ├── YouTube.tsx     # Video browser + playback controls
│   │           │   └── Settings.tsx    # Credentials, overlay, and display settings
│   │           ├── components/
│   │           │   ├── Sidebar.tsx     # Navigation sidebar
│   │           │   └── NowPlayingBar.tsx
│   │           └── hooks/
│   │               └── useTvAutomator.tsx  # Global state + WebSocket
│   ├── providers/
│   │   ├── base.py                    # Provider interface (Game, Team, GameStatus)
│   │   ├── mlb.py                     # MLB schedule (Stats API)
│   │   └── mlb_session.py             # MLB auth + streams (Okta + GraphQL)
│   ├── automator/
│   │   └── browser_control.py         # Chrome window management
│   └── scheduler/
│       └── game_scheduler.py          # Background schedule polling
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
