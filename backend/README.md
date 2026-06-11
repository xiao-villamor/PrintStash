# PrintStash — Backend

FastAPI backend for the vault. Ingests STL / 3MF / OBJ / G-code, extracts
metadata, deduplicates by hash, and serves everything through a versioned
REST API. Used by the Next.js frontend and the OrcaSlicer post-processing
hook.

See the [root README](../README.md) for the big picture.

## Stack

- Python 3.11+, FastAPI, SQLModel, Uvicorn
- SQLite by default, optional Postgres for larger installs
- Trimesh for mesh geometry, thumbnails, and cached STL conversion

## Quick start (development)

```bash
cd backend

# First time — create venv and install deps
uv sync --extra dev

# Run the server
VAULT_DB_URL=sqlite:///./dev.sqlite \
VAULT_DATA_DIR=./_data/files \
VAULT_THUMB_DIR=./_data/thumbs \
uv run uvicorn app.main:app --reload
```

Open <http://localhost:8000/docs> for the Swagger UI.

## Migrations

Schema upgrades run through Alembic so self-hosted installs have a
predictable upgrade path.

```bash
cd backend

# Apply the latest schema to your configured database
uv run alembic upgrade head

# Stamp an existing database that already matches the baseline
uv run alembic stamp head
```

### Upgrade flow (local dev)

```bash
cd backend
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

### Upgrade flow (Docker)

```bash
# Stop old containers (keeps volumes)
docker compose down

# Run DB migrations against current VAULT_DB_URL
docker compose run --rm api uv run alembic upgrade head

# Start updated stack
docker compose up -d --build
```

### SQLite -> Postgres migration helper

Use the optional script at `scripts/sqlite_to_postgres.py` after migrating the
target Postgres schema to head:

```bash
cd backend
uv run alembic upgrade head

cd ..
python scripts/sqlite_to_postgres.py \
  --sqlite sqlite:////absolute/path/to/printstash.sqlite \
  --postgres postgresql://printstash:printstash@localhost:5432/printstash
```

## Layout

```
backend/
├── pyproject.toml
├── Dockerfile
└── app/
    ├── main.py            ← FastAPI app, lifespan, Starlette
    ├── core/              ← config, security, logging, time, http helpers
    ├── db/                ← SQLModel tables, session, DB bootstrap
    ├── schemas/           ← Pydantic DTOs
    ├── services/          ← business logic
    └── api/v1/            ← routers (files, ingest, models, printers, taxonomy, auth, backups, config)
```

## Environment variables

| Variable | What it does | Example |
|---|---|---|
| `VAULT_DB_URL` | Database connection string | `sqlite:///./dev.sqlite` |
| `VAULT_DATA_DIR` | Where ingested files live | `./_data/files` |
| `VAULT_THUMB_DIR` | Where rendered thumbnails go | `./_data/thumbs` |
| `VAULT_JWT_SECRET` | Signing key for auth tokens | anything random |
| `VAULT_JWT_ALGORITHM` | JWT algorithm | `HS256` |
| `VAULT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token lifetime | `60` |
| `VAULT_MAX_UPLOAD_MB` | Upload size limit | `512` |
| `VAULT_LOG_LEVEL` | Python log level | `INFO` |

> The first admin user is created via the web-based first-run wizard at
> `/setup` — there is no env-driven default account. Storage paths above are
> the defaults; the wizard can override `data_dir` / `thumb_dir` per install
> and persists them in the `system_config` table.

## Docker

From the repo root:

```bash
docker compose up --build
```

The compose file mounts named volumes under `/data/` so your files and DB
survive container rebuilds.

For upgrades, run migrations before starting a new backend image:

```bash
docker compose run --rm api uv run alembic upgrade head
```

## Postgres (optional adapter)

Choose Postgres when you expect higher write concurrency, larger datasets, or
you need operational tooling around backups/replication that goes beyond a
single SQLite file. Stay on SQLite when you are running single-node and want
the simplest self-hosted setup.

Use the built-in profile:

```bash
docker compose --profile postgres up -d postgres
```

Then set:

```bash
VAULT_DB_URL=postgresql://printstash:printstash@postgres:5432/printstash
```

Run migrations before starting the API:

```bash
docker compose run --rm api uv run alembic upgrade head
```

## S3 / S3-compatible storage (optional feature)

Local filesystem storage remains the default for self-hosted installs. S3 is
an optional adapter for operators who want object storage semantics.

Supported endpoints include AWS S3, Cloudflare R2, and MinIO.

### MinIO for local testing

```bash
docker compose --profile s3 up -d minio
```

Then configure:

```bash
VAULT_STORAGE_BACKEND=s3
VAULT_S3_BUCKET=printstash-vault
VAULT_S3_ENDPOINT_URL=http://minio:9000
VAULT_S3_REGION=us-east-1
VAULT_S3_ACCESS_KEY=minioadmin
VAULT_S3_SECRET_KEY=minioadmin
```

### Storage and file capabilities

- S3 health probe exposed via `GET /api/v1/health`
- Multipart upload threshold (default: 50MB) via `VAULT_S3_MULTIPART_THRESHOLD_MB`
- Pre-signed direct downloads:
  - `GET /api/v1/files/{id}/download-url`
  - `GET /api/v1/files/{id}/download-direct`
- Cached mesh preview conversion:
  - `GET /api/v1/files/{id}/stl`
- Thumbnail rebuild job:
  - `POST /api/v1/files/thumbnails/rebuild`
- Optional bucket lifecycle policy:
  - `VAULT_S3_LIFECYCLE_EXPIRATION_DAYS`
  - `VAULT_S3_LIFECYCLE_TRANSITION_DAYS`
  - `VAULT_S3_TRANSITION_STORAGE_CLASS`

## Tests

```bash
uv run pytest tests
```

Tests use `tmp_path` — they never touch the real data volume.
