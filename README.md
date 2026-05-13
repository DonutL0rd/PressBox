# PressBox

Portable self-hosted media control plane — one container, runs anywhere (laptop, NAS, VPS, cloud VM), serving MLB.TV, Navidrome, and YouTube through a unified web app. Any browser on the network opens either the **dashboard** (control surface) or the **kiosk** (full-screen playback view) — playback happens in the client browser, the server is just the brain.

## What It Does

1. **Start the Docker container** anywhere — laptop, NAS, VPS, cloud VM, your old Mac mini
2. **Open the dashboard** at `http://<server-ip>:5050/` from any device — phone, tablet, laptop
3. **Open `/kiosk`** in whichever browser you want playback to happen in (a TV-connected mini PC, a Chromebook, a spare iPad)
4. **Watch MLB games** — live HLS streams with home/away feed selection, condensed game replays, pitch tracker, and batter intel overlays
5. **Play music** — browse and queue from Navidrome/Subsonic; audio plays in the kiosk tab
6. **Watch YouTube** — paste a URL or browse suggested channels; watch history with position resume

When idle, an ambient screensaver cycles through the day's MLB schedule with scores, innings, venue, and probable pitchers. When music is playing, the layout splits: album art and track metadata on the left, schedule carousel on the right.

Authentication with MLB.TV is handled entirely via API (Okta password grant) — no browser login required.

## Quick Start

### Prerequisites

- A machine that runs Docker (anything — laptop, NAS, VPS, cloud VM, Raspberry Pi)
- Docker & Docker Compose
- A second device with a modern browser to open `/kiosk` on (the "TV"). Same device is fine.
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

### 2. Start the container

```bash
cd docker
docker compose up -d
```

### 3. Open the dashboard

Go to `http://<server-ip>:5050/` in any browser. Click **Home** or **Away** on any live game.

### 4. Open the kiosk on whatever you want to be "the TV"

On the device you want playback to appear on, open `http://<server-ip>:5050/kiosk` and put the tab in fullscreen. Streams, music, and YouTube routed from the dashboard play here. Any number of devices can open the dashboard; pick one to be the kiosk.

> **macOS note:** the default host port is **5050** because macOS AirPlay Receiver squats on `:5000`. Change the host side of `ports:` in `docker-compose.yml` if you need a different port.

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
    ┌─────────────────┐         ┌──────────────────────────┐
    │ Dashboard tab   │         │ Kiosk tab (full-screen)  │
    │ (phone/laptop)  │         │ HLS / audio / YouTube    │
    └────────┬────────┘         └────────────┬─────────────┘
             │  http://:5050                 │  WebSocket /ws
             ▼                               ▼
    ┌───────────────────────────────────────────────────────┐
    │  Docker Container — runs anywhere                     │
    │                                                       │
    │  ┌─────────────────────────────────────────────────┐  │
    │  │  FastAPI + uvicorn (port 5000 inside)           │  │
    │  │                                                 │  │
    │  │  GET  /              → React SPA (Dashboard)    │  │
    │  │  GET  /kiosk         → Client-side playback     │  │
    │  │  GET  /api/games     → MLB schedule             │  │
    │  │  POST /api/play      → Resolve stream → push    │  │
    │  │  POST /api/stop      → Stop playback            │  │
    │  │  POST /api/youtube   → Play YouTube video       │  │
    │  │  GET  /api/music/*   → Music library & control  │  │
    │  │  POST /api/music/*   → Playback + queue control │  │
    │  │  GET  /player        → HLS player + overlays    │  │
    │  │  GET  /screensaver   → Ambient schedule display │  │
    │  │  GET  /tv/youtube    → YouTube player page      │  │
    │  │  GET  /hls/*         → HLS proxy (CORS bypass)  │  │
    │  │  WS   /ws            → Real-time state push     │  │
    │  └──────┬──────────────────┬─────────────────────────┘  │
    │         ▼                  ▼                         │
    │  ┌──────────────┐  ┌────────────────────┐           │
    │  │ MLBSession   │  │ Navidrome Client   │           │
    │  │ (Okta auth + │  │ (Subsonic API)     │           │
    │  │  GraphQL)    │  └────────────────────┘           │
    │  └──────────────┘                                    │
    └───────────────────────────────────────────────────────┘
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
press_box/
├── Makefile                              # make install / make test / make clean
├── docker/
│   ├── Dockerfile                        # python:3.12-slim base, ~300 MB image
│   ├── docker-compose.yml
│   └── entrypoint.sh
├── src/tv_automator/
│   ├── main.py                           # Entry point (uvicorn)
│   ├── settings.py                       # Settings store ($DATA_DIR/settings.json)
│   ├── web/
│   │   ├── app.py                        # FastAPI app wiring + WebSocket hub
│   │   ├── player.py                     # MLB playback, HLS proxy, heartbeat
│   │   ├── music.py                      # Subsonic/Navidrome music API + queue
│   │   ├── youtube.py                    # YouTube playback + watch history
│   │   ├── pitch_data.py                 # Pitch / batter-intel parsing (pure)
│   │   ├── templates/
│   │   │   ├── kiosk.html                # Full-screen client-side playback
│   │   │   ├── player.html               # HLS video player + pitch overlays
│   │   │   ├── screensaver.html          # Ambient schedule + music display
│   │   │   └── youtube.html              # YouTube player page
│   │   └── frontend/                     # React SPA (Vite + TypeScript)
│   │       └── src/
│   │           ├── views/
│   │           │   ├── Dashboard.tsx     # Game list + stream controls
│   │           │   ├── Music.tsx         # Music library + transport bar
│   │           │   ├── YouTube.tsx       # Video browser + watch history
│   │           │   └── Settings.tsx      # Credentials + display settings
│   │           ├── components/
│   │           │   ├── Sidebar.tsx       # Navigation sidebar
│   │           │   └── NowPlayingBar.tsx # Persistent now-playing strip
│   │           └── hooks/
│   │               └── useTvAutomator.tsx# Global state + WebSocket
│   ├── providers/
│   │   ├── base.py                       # Provider interface
│   │   ├── mlb.py                        # MLB schedule (Stats API)
│   │   └── mlb_session.py                # MLB auth + streams (Okta + GraphQL)
│   ├── automator/
│   │   └── browser_control.py            # (legacy, gated by ENABLE_LOCAL_BROWSER)
│   └── scheduler/
│       └── game_scheduler.py             # Schedule polling + auto-start
├── tests/                                # Pytest suite
│   ├── conftest.py
│   ├── test_base.py
│   ├── test_hls_proxy.py
│   ├── test_mlb_provider.py
│   ├── test_mlb_session.py
│   ├── test_pitch_data.py
│   ├── test_player.py
│   ├── test_scheduler.py
│   └── test_settings.py
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
# Local dev (without Docker)
make install                          # builds .venv with editable install
cp .env.example .env                  # fill in credentials
.venv/bin/python -m tv_automator.main # serve on :5000
make test                             # run the pytest suite
```

## License

MIT
