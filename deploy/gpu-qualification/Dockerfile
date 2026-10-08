# Research image: no API or production Job selects the candidate.
FROM ghcr.io/astral-sh/uv:0.12.24 AS uv
FROM python:3.14.8-slim
COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends     libvulkan1 mesa-vulkan-drivers libegl1 libgles2     && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/printstash/backend
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/packages/printstash-core/pyproject.toml packages/printstash-core/
COPY backend/packages/printstash-core/src packages/printstash-core/src
RUN uv sync --frozen --no-dev --extra webgpu-pilot
COPY backend/app app
COPY backend/scripts scripts
ENV HOME=/tmp XDG_RUNTIME_DIR=/tmp UV_CACHE_DIR=/tmp/uv-cache
USER 65532:65532
ENTRYPOINT ["/opt/printstash/backend/.venv/bin/python", "-m", "scripts.webgpu_doctor"]
