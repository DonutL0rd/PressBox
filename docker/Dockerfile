# ── Frontend Build Stage ─────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY src/tv_automator/web/frontend/package*.json ./
RUN npm install
COPY src/tv_automator/web/frontend/ ./
RUN npm run build

# ── TV-Automator Docker Image ────────────────────────────────────
# Ubuntu base with Google Chrome (Widevine DRM), Python/FastAPI,
# nginx reverse proxy, and a minimal window manager for HDMI output.
# ─────────────────────────────────────────────────────────────────

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=en_US.UTF-8
ENV TZ=America/Los_Angeles

# ── System packages ──────────────────────────────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg wget \
    python3.11 python3.11-venv python3-pip \
    xdg-utils openbox xdotool x11-utils xvfb \
    pulseaudio alsa-utils cec-utils mpv \
    fonts-liberation fonts-noto \
    locales dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8

# ── Google Chrome (for Widevine DRM support) ─────────────────────

RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
    http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# ── Python setup ─────────────────────────────────────────────────

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml /app/
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . 2>/dev/null || true

# Install Playwright browsers (chromium — used as fallback only)
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

# ── Copy application code ────────────────────────────────────────

COPY src/ /app/src/
# Copy the built React assets from the node buider stage
COPY --from=frontend-builder /app/frontend/dist/ /app/src/tv_automator/web/frontend/dist/

RUN pip install --no-cache-dir -e .

# ── Openbox configuration ────────────────────────────────────────

RUN mkdir -p /root/.config/openbox
COPY docker/openbox-config/rc.xml /root/.config/openbox/rc.xml

# ── Data volume ──────────────────────────────────────────────────

VOLUME /data
ENV DATA_DIR=/data

EXPOSE 5000

# ── Entrypoint ───────────────────────────────────────────────────

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
