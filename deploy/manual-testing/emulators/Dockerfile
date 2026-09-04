# Build the checked-in provider testkit once; optional emulator services then
# run without pip/npm downloads at container startup.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /build
RUN python -m venv /opt/venv
COPY backend/packages/printstash-core/pyproject.toml ./pyproject.toml
COPY backend/packages/printstash-core/src ./src
RUN /opt/venv/bin/pip install --no-cache-dir ".[testkit]"

WORKDIR /app
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin testkit
USER testkit

CMD ["python", "-m", "printstash_core_testkit.mock_printer", "--host", "0.0.0.0", "--port", "7125"]
