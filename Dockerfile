# syntax=docker/dockerfile:1.7
#
# PaperWrench single-container image (ADR-0001).
#
# The SPA is built once and copied into the Python package, so the runtime
# image serves the API and the frontend from one origin: no CORS, no reverse
# proxy required, one port.
#
# Built wheel, non-root startup and compiled SPA routing are verified in CI.

# ---------------------------------------------------------------------------
# Stage 1 - build the SPA
# ---------------------------------------------------------------------------
FROM node:20-bookworm-slim AS frontend

WORKDIR /build

# Install dependencies from the lockfile first so this layer caches.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# vite.config.ts writes to ../backend/src/paperwrench/static; the backend tree
# does not exist in this stage, so redirect the output explicitly.
RUN npx tsc -b && npx vite build --outDir /build/dist --emptyOutDir


# ---------------------------------------------------------------------------
# Stage 2 - build the Python wheel
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS backend

WORKDIR /build

RUN pip install --no-cache-dir build

COPY backend/ ./
COPY --from=frontend /build/dist ./src/paperwrench/static
RUN python -m build --wheel --outdir /wheels


# ---------------------------------------------------------------------------
# Stage 3 - runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="PaperWrench" \
      org.opencontainers.image.description="Power tools for Paperless-ngx. Independent project, not affiliated with Paperless-ngx." \
      org.opencontainers.image.source="https://github.com/flowent59/PaperWrench" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PAPERWRENCH_DATA_DIR=/data \
    PAPERWRENCH_DATABASE_URL=sqlite+pysqlite:////data/paperwrench.db \
    PAPERWRENCH_HOST=0.0.0.0 \
    PAPERWRENCH_PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root by default. The UID is fixed so a bind-mounted /data can be
# chowned predictably on the host.
RUN groupadd --gid 10001 paperwrench \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin paperwrench

COPY --from=backend /wheels/*.whl /tmp/
# Alembic revisions live inside the package, so the wheel is self-contained
# and migrations run on boot without any extra files.
RUN pip install --no-cache-dir --upgrade "pip>=26.2" "setuptools>=83.0.0" \
    && pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

RUN mkdir -p /data /app && chown -R paperwrench:paperwrench /data /app

USER paperwrench
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/system/health || exit 1

# Single worker on purpose: the job engine holds a single-instance runtime
# lock and runs in-process (ADR-0006). More workers would be rejected at boot.
CMD ["uvicorn", "paperwrench.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
