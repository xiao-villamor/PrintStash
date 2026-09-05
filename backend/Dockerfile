ARG PRINTSTASH_VARIANT=full

FROM rust:1.91-slim-bookworm AS opendal-builder

ARG OPENDAL_SOURCE_VERSION=0.58.2
ARG OPENDAL_SOURCE_SHA256=476b0358ddc2a644dd4448552331af0d814279b0c1aaf5da755510b34645a764

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    python3 \
    python3-venv \
 && rm -rf /var/lib/apt/lists/*

# OpenDAL v0.58.2 publishes Python distribution 0.47.6. Its upstream Linux
# wheel omits the Unix-only SFTP service, so the full image builds the exact
# release source with only PrintStash's remote-storage contract surface enabled.
RUN curl -fsSL \
      "https://github.com/apache/opendal/archive/refs/tags/v${OPENDAL_SOURCE_VERSION}.tar.gz" \
      -o /tmp/opendal.tar.gz \
 && echo "${OPENDAL_SOURCE_SHA256}  /tmp/opendal.tar.gz" | sha256sum -c - \
 && mkdir -p /src /wheels \
 && tar -xzf /tmp/opendal.tar.gz -C /src --strip-components=1 \
 && python3 -m venv /tmp/maturin \
 && /tmp/maturin/bin/pip install --no-cache-dir 'maturin==1.9.6' \
 && cd /src/bindings/python \
 && /tmp/maturin/bin/maturin build --release --no-default-features \
      --features pyo3/extension-module,abi3,services-memory,services-s3,services-webdav,services-sftp,services-gdrive \
      --out /wheels

FROM scratch AS opendal-wheel
COPY --from=opendal-builder /wheels /

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/.uv_cache \
    UV_COMPILE_BYTECODE=1

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

FROM base AS dependencies-lite
RUN uv sync --frozen --no-dev --no-editable \
 && uv cache clean \
 && rm -rf "$UV_CACHE_DIR"

FROM base AS dependencies-full
COPY --from=opendal-builder /wheels /tmp/opendal-wheels
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && uv sync --frozen --no-dev --no-editable --extra full \
 && uv pip install --reinstall /tmp/opendal-wheels/opendal-0.47.6-*.whl \
 && .venv/bin/python -c 'import opendal; opendal.Operator("s3", bucket="build-check", region="us-east-1", access_key_id="build-check", secret_access_key="build-check", disable_config_load="true", disable_ec2_metadata="true"); opendal.Operator("webdav", endpoint="https://example.invalid", root="/"); opendal.Operator("gdrive", root="/", client_id="build-check", client_secret="build-check", refresh_token="build-check"); import asyncssh' \
 && rm -rf /tmp/opendal-wheels \
 && uv cache clean \
 && rm -rf "$UV_CACHE_DIR"

FROM dependencies-${PRINTSTASH_VARIANT} AS runtime
ARG PRINTSTASH_VARIANT

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin printstash \
 && mkdir -p /data/files /data/thumbs /data/staging /data/backups /data/db \
 && chown -R printstash:printstash /data

# Keep any legacy/operator-supplied uv command away from the root-owned
# build cache. The shipped entrypoint and CMD execute the virtualenv directly,
# but older Compose overrides must still be able to start as printstash.
ENV UV_CACHE_DIR=/tmp/printstash-uv-cache \
    UV_NO_SYNC=1

COPY --chown=printstash:printstash app ./app
COPY --chown=printstash:printstash --chmod=0644 alembic.ini ./alembic.ini
COPY --chown=printstash:printstash alembic ./alembic
COPY docker-entrypoint.sh ./docker-entrypoint.sh
# The entrypoint re-execs as the unprivileged runtime user, which needs read as
# well as execute permission regardless of the build context's local umask.
RUN chmod 0755 ./docker-entrypoint.sh

ENV VAULT_DATA_DIR=/data/files \
    VAULT_THUMB_DIR=/data/thumbs \
    VAULT_DB_URL=sqlite:////data/db/printstash.sqlite \
    PUID=10001 \
    PGID=10001 \
    PRINTSTASH_IMAGE_VARIANT=${PRINTSTASH_VARIANT}

EXPOSE 8000

# Entrypoint runs DB migrations, then execs CMD. Migrations therefore run on
# every container start regardless of the orchestrator's command (issue #29).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
