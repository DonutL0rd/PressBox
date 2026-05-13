# PressBox

Portable self-hosted media control plane — one container, runs anywhere (laptop, NAS, VPS, cloud VM), serving MLB.TV, Navidrome, and YouTube through a unified web app. Any browser on the network opens either the **dashboard** (control surface) or the **kiosk** (full-screen playback view) — playback happens in the client browser, the server is just the brain.

## What It Does

1. **Start the Docker container** on your server.
2. **Open the dashboard** at `http://<server-ip>:5000/` from any device.
3. **Watch on your TV** — Open `http://<server-ip>:5000/kiosk` in your TV's browser (or any tablet/screen) to turn it into a dedicated player.
4. **Watch MLB games** — live HLS streams with home/away feed selection, condensed game replays, pitch tracker, and batter intel overlays.
5. **Play music** — browse and queue from Navidrome/Subsonic; audio plays directly in the Kiosk browser.
6. **Watch YouTube** — paste a URL or browse suggested channels; watch history with position resume.

When idle, the Kiosk displays an ambient screensaver cycling through the day's MLB schedule. When music is playing, the layout splits: album art on the left, schedule on the right.

## Quick Start

### Prerequisites

- A machine that runs Docker (laptop, NAS, VPS, Raspberry Pi)
- A device with a modern browser to open `/kiosk` on (the "TV")
- An MLB.TV subscription

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

> **macOS note:** the default host port is **5000**. If you have macOS AirPlay Receiver enabled, it may squat on this port; you can change the host port in `docker-compose.yml` if needed.

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

### Roadmap

- [x] Phase 1: MLB game playback with web dashboard
- [x] Phase 2: API-based auth (Okta), HLS streaming, home/away feed selection
- [x] Phase 3: React SPA, music integration (Navidrome), YouTube playback
- [x] Phase 4: Ambient screensaver, pitch tracker, batter intel, between-innings overlays
- [ ] Phase 5: Multiview (picture-in-picture / split-screen)
- [ ] Phase 6: Additional providers (F1 TV, NBA, NHL, NFL)

## License

MIT
