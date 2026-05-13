# -- Frontend Build Stage -----------------------------------------------
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY src/tv_automator/web/frontend/package*.json ./
RUN npm install
COPY src/tv_automator/web/frontend/ ./
RUN npm run build

# -- PressBox Server Image ----------------------------------------------
# Portable headless server. Playback (HLS, audio, YouTube) runs in the
# client browser via /dashboard or /kiosk — no Chrome, no X server, no
# host audio stack required. Runs anywhere Docker runs.
# -----------------------------------------------------------------------

FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/Los_Angeles
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# -- Python deps --------------------------------------------------------

COPY pyproject.toml /app/
WORKDIR /app
RUN pip install --upgrade pip \
    && pip install . 2>/dev/null || true

# -- Application code ---------------------------------------------------

COPY src/ /app/src/
COPY --from=frontend-builder /app/frontend/dist/ /app/src/tv_automator/web/frontend/dist/

RUN pip install -e .

# -- Runtime ------------------------------------------------------------

VOLUME /data
ENV DATA_DIR=/data
ENV ENABLE_LOCAL_BROWSER=false

EXPOSE 5000

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
