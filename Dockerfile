# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
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
FROM node:22-trixie-slim@sha256:b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4 AS frontend

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
FROM python:3.11-slim-trixie@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b AS backend

WORKDIR /build

RUN pip install --no-cache-dir build==1.6.1 packaging==26.3 pyproject_hooks==1.3.3

COPY backend/ ./
COPY --from=frontend /build/dist ./src/paperwrench/static
RUN python -m build --wheel --outdir /wheels


# ---------------------------------------------------------------------------
# Stage 3 - runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim-trixie@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b AS runtime

# The pinned base predates the PCRE2 fix for CVE-2026-103111.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade libpcre2-8-0 \
    && rm -rf /var/lib/apt/lists/*

ARG VCS_REF
LABEL org.opencontainers.image.title="PaperWrench" \
      org.opencontainers.image.description="Power tools for Paperless-ngx. Independent project, not affiliated with Paperless-ngx." \
      org.opencontainers.image.source="https://github.com/flowent59/PaperWrench" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PAPERWRENCH_DATA_DIR=/data \
    PAPERWRENCH_DATABASE_URL=sqlite+pysqlite:////data/paperwrench.db \
    PAPERWRENCH_HOST=0.0.0.0 \
    PAPERWRENCH_PORT=8000

# Non-root by default. The UID is fixed so a bind-mounted /data can be
# chowned predictably on the host.
RUN groupadd --gid 10001 paperwrench \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin paperwrench

COPY --from=backend /wheels/*.whl /tmp/
COPY backend/requirements.lock /tmp/requirements.lock
# Alembic revisions live inside the package, so the wheel is self-contained
# and migrations run on boot without any extra files. Installation tools are
# unused at runtime. Check the installed application's dependencies after removing
# setuptools/wheel, then remove pip and its vulnerable vendored libraries too.
RUN pip install --no-cache-dir -r /tmp/requirements.lock \
    && pip install --no-cache-dir --no-deps /tmp/*.whl \
    && pip uninstall -y setuptools wheel \
    && pip check \
    && pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.11/ensurepip \
    && rm -f /tmp/*.whl /tmp/requirements.lock

RUN mkdir -p /data /app /run/secrets && chown -R paperwrench:paperwrench /data /app

USER paperwrench
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/system/health', timeout=5).read()" || exit 1

# Single worker on purpose: the job engine holds a single-instance runtime
# lock and runs in-process (ADR-0006). More workers would be rejected at boot.
CMD ["uvicorn", "paperwrench.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
