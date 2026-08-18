FROM python:3.11-slim

ARG PRINTSTASH_VARIANT=full

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
 && rm -rf /var/lib/apt/lists/*

COPY --chmod=0644 pyproject.toml uv.lock ./
COPY --chmod=0644 packages/printstash-core/pyproject.toml ./packages/printstash-core/pyproject.toml
COPY --chmod=0644 packages/printstash-core/src ./packages/printstash-core/src
RUN case "$PRINTSTASH_VARIANT" in \
      full) uv sync --frozen --no-dev --no-editable --extra full ;; \
      lite) uv sync --frozen --no-dev --no-editable ;; \
      *) echo "Unknown PRINTSTASH_VARIANT=$PRINTSTASH_VARIANT" >&2; exit 2 ;; \
    esac \
 && uv cache clean \
 && rm -rf "$UV_CACHE_DIR"

# Chromium for server-side rendering of Cloudflare-gated pages (MakerWorld
# imports). --with-deps pulls the OS libraries Chromium needs; the browser is
# stored at $PLAYWRIGHT_BROWSERS_PATH so the runtime finds it. This is the bulk
# of the image size. See app/services/browser_fetch.py.
RUN if [ "$PRINTSTASH_VARIANT" = "full" ]; then \
      uv run --no-sync patchright install --with-deps chromium; \
    fi

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin printstash \
 && mkdir -p /data/files /data/thumbs /data/staging /data/backups /data/db /opt/pw-browsers \
 && chown -R printstash:printstash /data /opt/pw-browsers

COPY --chown=printstash:printstash app ./app
COPY --chown=printstash:printstash --chmod=0644 alembic.ini ./alembic.ini
COPY --chown=printstash:printstash alembic ./alembic
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh

ENV VAULT_DATA_DIR=/data/files \
    VAULT_THUMB_DIR=/data/thumbs \
    VAULT_DB_URL=sqlite:////data/db/printstash.sqlite \
    PRINTSTASH_IMAGE_VARIANT=${PRINTSTASH_VARIANT}

EXPOSE 8000

# Entrypoint runs DB migrations, then execs CMD. Migrations therefore run on
# every container start regardless of the orchestrator's command (issue #29).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
