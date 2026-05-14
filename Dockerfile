# 5.8: production-ish container image. Multi-stage so the runtime
# layer doesn't drag in pip / build toolchain. The image is single
# purpose — runs `uvicorn main:app` on :8000.
#
#   docker build -t par2 .
#   docker run --rm -p 8000:8000 \
#       --env-file .env \
#       -v "$PWD/data:/data" \
#       par2
#
# Persistent state (the SQLite db and requests-cache files) lives
# under /data inside the container. Mount a host directory there
# (or use the docker-compose.yml in this repo) to avoid losing it
# when the container is replaced.

# ---- frontend builder -------------------------------------------------------
# ROADMAP Stage 10.7z — produce the Vite/Vue 3/TS bundle in a dedicated
# stage so the runtime image doesn't drag in Node. The runtime stage
# copies the emitted `dist/` into `/app/frontend/dist`, which `main.py`
# mounts at `/`. If this stage is ever skipped (e.g. on a build target
# that omits the frontend), the mount silently no-ops and `/` will
# 404 — there is no legacy fallback after the index.html retirement.
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

# package*.json copied first so the npm-install layer is cached when only
# source files change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---- python builder ---------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install build deps for any wheels that need a C compiler. lxml /
# parse-torrent-name don't need this on slim, but adding the meta
# package keeps the build resilient if a transitive dep changes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer) before copying the rest
# of the source — this way a code-only change re-uses the heavy
# pip-install layer.
COPY requirements.txt .
RUN pip install --user --no-warn-script-location -r requirements.txt

# ---- runtime ----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/par2/.local/bin:${PATH}" \
    APP_DATA_DIR=/data

# Run as non-root. The /data mountpoint is owned by the same user
# so the volume is writable without `--user 0:0`.
RUN useradd --create-home --uid 1000 par2 \
    && mkdir -p /data \
    && chown par2:par2 /data

# Carry over the user-installed packages from the builder stage.
COPY --from=builder --chown=par2:par2 /root/.local /home/par2/.local

USER par2
WORKDIR /app

# Copy source after deps so source-only changes don't bust the
# pip-install layer cache.
COPY --chown=par2:par2 . .

# Bring the Vite-built SPA from the frontend-builder stage into the
# location `main.py` looks for (`frontend/dist`). The `frontend/` directory
# copied above already contains source files but no `dist/` (it's in
# `.dockerignore` / `.gitignore`); the COPY below materialises the build
# output. With this in place the FastAPI app mounts the bundle at `/`.
COPY --from=frontend-builder --chown=par2:par2 /frontend/dist /app/frontend/dist

# 8000 matches main.py's uvicorn.run(..., port=8000).
EXPOSE 8000

# 6.5 — dedicated /health endpoint (does a SELECT 1 against the DB
# and reports user_version). It's exempt from auth so probes work
# regardless of AUTH_USER. Returns 503 if the DB is unreachable;
# otherwise 200, which docker reads as healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)" \
        || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio"]
