FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/.uv_cache \
    UV_COMPILE_BYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    curl \
    gosu \
    libxml2-dev \
    libxslt1-dev \
    libfreetype6-dev \
    libpng-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

# Chromium for server-side rendering of Cloudflare-gated pages (MakerWorld
# imports). --with-deps pulls the OS libraries Chromium needs; the browser is
# stored at $PLAYWRIGHT_BROWSERS_PATH so the runtime finds it. This is the bulk
# of the image size. See app/services/browser_fetch.py.
RUN uv run patchright install --with-deps chromium

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin printstash \
 && mkdir -p /data/files /data/thumbs /data/staging /data/backups /data/db \
 && chown -R printstash:printstash /data /opt/pw-browsers "$UV_CACHE_DIR"

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

ENV VAULT_DATA_DIR=/data/files \
    VAULT_THUMB_DIR=/data/thumbs \
    VAULT_DB_URL=sqlite:////data/db/printstash.sqlite

EXPOSE 8000

# Entrypoint runs DB migrations, then execs CMD. Migrations therefore run on
# every container start regardless of the orchestrator's command (issue #29).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
