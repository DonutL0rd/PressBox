# PressBox

Self-hosted streaming appliance for your TV. Runs in a lightweight Docker container. Control everything from a web dashboard and watch on any device or a Smart TV in "Kiosk" mode.

## What It Does

1. **Start the Docker container** on your server.
2. **Open the dashboard** at `http://<server-ip>:5000/` from any device.
3. **Watch on your TV** — Open `http://<server-ip>:5000/kiosk` in your TV's browser (or any tablet/screen) to turn it into a dedicated player.
4. **Watch MLB games** — live HLS streams with home/away feed selection, condensed game replays, pitch tracker, and batter intel overlays.
5. **Play music** — browse and queue from Navidrome/Subsonic; audio plays server-side via mpv + PulseAudio.
6. **Watch YouTube** — paste a URL or browse suggested channels; watch history with position resume.

When idle, the Kiosk displays an ambient screensaver cycling through the day's MLB schedule. When music is playing, the layout splits: album art on the left, schedule on the right.

## Quick Start

### 1. Clone & configure

```bash
git clone <repo-url> PressBox
cd PressBox
cp .env.example .env
```

Edit `.env` and add your MLB.TV credentials:

```
MLB_USERNAME=you@example.com
MLB_PASSWORD=yourpassword
```

### 2. Start the container

```bash
docker compose up -d
```

### 3. Open the dashboard

Go to `http://<server-ip>:5000/` in your browser.

### 4. Setup the Player (Kiosk)

On the device connected to your TV (or the Smart TV itself), open:
`http://<server-ip>:5000/kiosk`

## How It Works

### Remote Player (Kiosk)

PressBox is designed as a "headless" backend that controls a "remote player" (the Kiosk). The Kiosk is just a web page that reacts to commands from the backend via WebSockets. This means you can run the backend on a low-power server (like a NAS or Pi) and use any browser-capable device as the screen.

### Authentication

TV-Automator authenticates with MLB.TV via Okta's resource owner password grant — the same API that official MLB apps use internally. No browser-based login, no CAPTCHAs, no fragile form-filling.

### MLB Playback

When you click a game in the dashboard, the backend fetches the HLS stream URL and broadcasts a "play" event. The Kiosk receives this and starts playback using `hls.js`. A server-side HLS proxy (`/hls/`) is used to bypass CORS restrictions.

### Music

Music is browsed and queued via the dashboard (Subsonic/Navidrome API). In the modern remote-first architecture, **audio plays directly in the Kiosk browser** (the TV or tablet), ensuring zero hardware dependencies on the backend server.

## Development

PressBox supports modern **Docker Compose Watch** for a high-velocity development loop.

```bash
# Start in watch mode
DEBUG=true docker compose watch
```

- **Backend:** Changes to `./src` (except frontend) are synced, and the server hot-reloads via Uvicorn.
- **Frontend:** Changes to `./src/tv_automator/web/frontend/src` trigger an automatic production build and container refresh.
- **Dependencies:** Changes to `pyproject.toml` trigger a container rebuild.

*Note: The original upstream Docker configuration is still available in the `docker/` directory for compatibility.*

## Architecture

```
                Browser (laptop/phone)
                         │
                    http://:5000 (Dashboard)
                         │
┌────────────────────────┼──────────────────────────────┐
│  Docker Container      │                              │
│                        │                              │
│  ┌─────────────────────▼───────────────────────────┐  │
│  │  FastAPI + uvicorn (port 5000)                  │  │
│  │                                                 │  │
│  │  /api/*       → Business Logic                  │  │
│  │  /hls/*       → HLS Proxy                       │  │
│  │  /ws          → Real-time State Hub             │  │
│  └──────┬───────────────────────┬──────────────────┘  │
│         │                       │                     │
│  ┌──────▼───────┐        ┌──────▼───────────┐         │
│  │ MLBSession   │        │ Navidrome Client │         │
│  │ (Okta Auth)  │        │ (Subsonic API)   │         │
│  └──────────────┘        └──────────────────┘         │
└───────────────────────────────┬───────────────────────┘
                                │
                                │ WebSocket / HTTP
                                │
                         ┌──────▼──────┐
                         │   Kiosk     │
                         │ (Web Player)│
                         └──────┬──────┘
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
| `DATA_DIR`           | No       | Persistent data path (default: `/data`)                 |
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
