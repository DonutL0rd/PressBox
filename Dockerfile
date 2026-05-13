# ── Frontend Build Stage ─────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY src/tv_automator/web/frontend/package*.json ./
RUN npm install
COPY src/tv_automator/web/frontend/ ./
RUN npm run build

# ── TV-Automator Docker Image ────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 1. Install build-time tools
RUN pip install --no-cache-dir hatchling

# 2. Install Python dependencies first (caching)
COPY pyproject.toml ./
RUN mkdir -p src/tv_automator && touch src/tv_automator/__init__.py
RUN pip install --no-cache-dir .

# 3. Copy source code (except what is in .dockerignore)
COPY src/ /app/src/

# 4. Inject built React assets from the node builder stage
# We do this AFTER copying src/ so that we overwrite any stale local dist
COPY --from=frontend-builder /app/frontend/dist/ /app/src/tv_automator/web/frontend/dist/

# 5. Install the application in editable mode
# This allows 'docker compose watch' to sync code changes into the container
RUN pip install --no-cache-dir -e .

# Data volume
VOLUME /data
ENV DATA_DIR=/data
ENV ENABLE_LOCAL_BROWSER=false
ENV PYTHONUNBUFFERED=1

EXPOSE 5000

# Start TV-Automator
CMD ["tv-automator"]
