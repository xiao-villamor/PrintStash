ARG PRINTSTASH_VARIANT=full

FROM rust:1.91-slim-bookworm AS opendal-builder

# OpenDAL core v0.59.5 publishes Python distribution 0.47.12, the version
# pinned in pyproject.toml. OPENDAL_SERVICES lists exactly the OpenDAL services
# registered in app/modules/storage/transports; tests/repo/test_storage_image.py
# keeps all three in step.
ARG OPENDAL_SOURCE_VERSION=0.59.5
ARG OPENDAL_SOURCE_SHA256=dd543a506488098ac698266a8dba0f6b33f6455d73383e0496094771ba0af4ab
ARG OPENDAL_SERVICES=services-memory,services-s3,services-webdav,services-gdrive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    python3 \
    python3-venv \
 && rm -rf /var/lib/apt/lists/*

# SFTP uses AsyncSSH; native amd64/arm64 image recovery proves this path.
# Build the exact OpenDAL release with only the registered service features,
# independently of the features selected in upstream wheels.
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
      --features "pyo3/extension-module,abi3,${OPENDAL_SERVICES}" \
      --out /wheels

FROM scratch AS opendal-wheel
COPY --from=opendal-builder /wheels /

FROM python:3.14.10-slim-trixie AS bgcode-builder
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git cmake g++ make libboost-dev libboost-nowide-dev zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*
COPY scripts/build-bgcode.sh /tmp/build-bgcode.sh
RUN sh /tmp/build-bgcode.sh

FROM scratch AS bgcode-binary
COPY --from=bgcode-builder /opt/libbgcode/bin/bgcode /bgcode

FROM python:3.14.10-slim-trixie AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_CACHE_DIR=/tmp/.uv_cache \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d /uv /usr/local/bin/uv

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    curl \
    libstdc++6 zlib1g \
    # bsdtar decompresses RAR members for rarfile; stored members need no tool.
    libarchive-tools \
 && rm -rf /var/lib/apt/lists/*

COPY --from=bgcode-builder /opt/libbgcode/bin/bgcode /usr/local/bin/bgcode
COPY --from=bgcode-builder /opt/libbgcode/share/licenses/libbgcode /usr/local/share/licenses/libbgcode

COPY --chmod=0644 pyproject.toml uv.lock ./
COPY --chmod=0644 packages/printstash-core/pyproject.toml ./packages/printstash-core/pyproject.toml

FROM base AS dependencies-common
# The heavy transitive dependencies depend on lockfiles, not application source.
RUN uv sync --frozen --no-dev --no-editable --no-install-workspace

FROM dependencies-common AS dependencies-lite
COPY --chmod=0644 packages/printstash-core/src ./packages/printstash-core/src
RUN uv sync --frozen --no-dev --no-editable \
 && uv cache clean \
 && rm -rf "$UV_CACHE_DIR"

FROM dependencies-common AS dependencies-full
ARG OPENDAL_PYTHON_VERSION=0.47.12
COPY --from=opendal-builder /wheels /tmp/opendal-wheels
# One image serves every host. The GPU runtime (wgpu, ONNX Runtime's WebGPU
# provider and the Vulkan/Mesa userspace) is always installed and does nothing
# until a device is handed to the container: with no device, discovery finds
# only a software adapter and every operation stays on the CPU. Handing a device
# in is a runtime choice (see docs/compute.md), never a different image.
# OpenCASCADE's Python binding links libGL.so.1 even without VTK; without
# libgl1 every STEP B-rep read (volume, similarity fingerprint) is unavailable.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client libvulkan1 mesa-vulkan-drivers libegl1 libgl1 libgl1-mesa-dri libglx-mesa0 \
 && rm -rf /var/lib/apt/lists/* \
 && uv sync --frozen --no-dev --no-editable --extra full --extra gpu --no-install-workspace
COPY --chmod=0644 packages/printstash-core/src ./packages/printstash-core/src
RUN uv sync --frozen --no-dev --no-editable --extra full --extra gpu \
 && uv pip install --reinstall "/tmp/opendal-wheels/opendal-${OPENDAL_PYTHON_VERSION}-"*.whl \
 && .venv/bin/python -c 'import opendal, asyncssh, wgpu, onnxruntime, OCP.BRep, OCP.STEPControl' \
 && rm -rf /tmp/opendal-wheels \
 && uv cache clean \
 && rm -rf "$UV_CACHE_DIR"

FROM dependencies-${PRINTSTASH_VARIANT} AS runtime
ARG PRINTSTASH_VARIANT
ARG PRINTSTASH_DBOS_NOTIFICATIONS=true

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin printstash \
 && mkdir -p /data/files /data/thumbs /data/staging /data/backups /data/db \
 && chown -R printstash:printstash /data

# Keep any legacy/operator-supplied uv command away from the root-owned
# build cache. The shipped entrypoint and CMD execute the virtualenv directly,
# but older Compose overrides must still be able to start as printstash.
ENV UV_CACHE_DIR=/tmp/printstash-uv-cache \
    UV_NO_SYNC=1

COPY --chown=printstash:printstash app ./app
COPY scripts/experimental_dbos_notifications.py ./scripts/experimental_dbos_notifications.py
RUN case "$PRINTSTASH_DBOS_NOTIFICATIONS" in \
      true) .venv/bin/python scripts/experimental_dbos_notifications.py \
        "$(.venv/bin/python -c 'import importlib.util; print(next(iter(importlib.util.find_spec("dbos").submodule_search_locations)))')" ;; \
      false) ;; \
      *) echo "PRINTSTASH_DBOS_NOTIFICATIONS must be true or false" >&2; exit 1 ;; \
    esac
# The image turns notifications on exactly when it carries the verified patch;
# source installs keep the unpatched SDK and the setting's default of false.
ENV VAULT_JOBS_SQLITE_QUEUE_NOTIFICATIONS=${PRINTSTASH_DBOS_NOTIFICATIONS}
# Fresh guarded parsers share code bytes, never interpreter or parser state.
# Runtime bytecode writes are disabled, so compile application sources at build.
RUN .venv/bin/python -m compileall -q app
# The full image must construct every registered OpenDAL service from its
# compiled wheel; the registry, not this file, names those services.
RUN case "$PRINTSTASH_VARIANT" in \
      full) .venv/bin/python -m app.modules.storage.transports.compiled ;; \
      lite) ;; \
      *) echo "PRINTSTASH_VARIANT must be full or lite" >&2; exit 1 ;; \
    esac
COPY --chown=printstash:printstash --chmod=0644 alembic.ini ./alembic.ini
COPY --chown=printstash:printstash alembic ./alembic
COPY docker-entrypoint.sh ./docker-entrypoint.sh
# The entrypoint re-execs as the unprivileged runtime user, which needs read as
# well as execute permission regardless of the build context's local umask.
RUN chmod 0755 ./docker-entrypoint.sh

# Mount one volume here. Every app-owned path (database, files, thumbnails,
# staging, backups, caches) is a child, and staging must share that mount for
# imports to publish by hard link instead of copying.
ENV VAULT_DATA_ROOT=/data \
    PUID=10001 \
    PGID=10001 \
    PRINTSTASH_IMAGE_VARIANT=${PRINTSTASH_VARIANT}

EXPOSE 8000

# Entrypoint runs DB migrations, then execs CMD. Migrations therefore run on
# every container start regardless of the orchestrator's command (issue #29).
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
