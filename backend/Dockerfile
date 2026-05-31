# -- Rust native extension builder -------------------------------------------
FROM rust:1-slim-bookworm AS rust-builder

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    python3-venv \
 && rm -rf /var/lib/apt/lists/*

# Build maturin + the nexus3d-rust wheel in one shot.
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir maturin

WORKDIR /build
COPY rust/Cargo.toml rust/Cargo.lock rust/pyproject.toml ./
COPY rust/src/ ./src/

RUN /opt/venv/bin/maturin build --release --out /wheels


# -- Python runtime ----------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/.uv_cache \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    curl \
    libxml2-dev \
    libxslt1-dev \
    libfreetype6-dev \
    libpng-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

# Install the pre-built Rust acceleration module.
COPY --from=rust-builder /wheels/*.whl /tmp/
RUN uv pip install --python .venv/bin/python /tmp/nexus3d_rust-*.whl && rm /tmp/*.whl

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

ENV VAULT_DATA_DIR=/data/files \
    VAULT_THUMB_DIR=/data/thumbs \
    VAULT_DB_URL=sqlite:////data/db/nexus3d.sqlite

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
