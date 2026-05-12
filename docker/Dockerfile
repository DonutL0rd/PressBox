# -- Frontend Build Stage -----------------------------------------------
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY src/tv_automator/web/frontend/package*.json ./
RUN npm install
COPY src/tv_automator/web/frontend/ ./
RUN npm run build

# -- TV-Automator Docker Image ------------------------------------------
# Debian slim base with Google Chrome (Widevine DRM), Python/FastAPI,
# and a minimal window manager for HDMI output.
# -----------------------------------------------------------------------

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=en_US.UTF-8
ENV TZ=America/Los_Angeles

# -- System packages + Google Chrome (single layer) ---------------------

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
        python3 python3-venv python3-pip \
        openbox xdotool x11-utils xvfb xdg-utils \
        pulseaudio alsa-utils cec-utils mpv \
        fonts-liberation \
        locales dbus-x11 \
    # -- Google Chrome (for Widevine DRM support) --
    && curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
        http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    # -- Cleanup: remove setup-only tools and caches --
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && locale-gen

# -- Python setup -------------------------------------------------------

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml /app/
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . 2>/dev/null || true

# Playwright controls the system Chrome — no browser download needed
RUN pip install --no-cache-dir playwright

# -- Copy application code ----------------------------------------------

COPY src/ /app/src/
# Copy the built React assets from the node builder stage
COPY --from=frontend-builder /app/frontend/dist/ /app/src/tv_automator/web/frontend/dist/
COPY config/ /app/config/

RUN pip install --no-cache-dir -e .

# -- Openbox configuration ----------------------------------------------

RUN mkdir -p /root/.config/openbox
COPY docker/openbox-config/rc.xml /root/.config/openbox/rc.xml

# -- Data volume ---------------------------------------------------------

VOLUME /data
ENV DATA_DIR=/data

EXPOSE 5000

# -- Entrypoint ----------------------------------------------------------

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
