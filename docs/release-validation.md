# 1.0 Release Validation

Run these checks before tagging a 1.0.x release.

## Clean Install

```bash
cp .env.example .env
docker compose up -d --build
curl -fsS http://localhost:8000/api/v1/health
```

Expected:

- the setup page is reachable at `http://localhost:3000/setup`
- `/api/v1/health` returns version `1.0.0`
- health components include database, storage, backup, and printer providers

## Upgrade From Existing SQLite Volume

```bash
docker compose down
docker compose run --rm api uv run alembic upgrade head
docker compose up -d
```

Expected:

- existing models/files are still visible
- thumbnails still load
- a new G-code upload creates or updates the expected model

## Backend

```bash
cd backend
uv run ruff check app/ tests/
uv run pytest tests -v
```

## Frontend

```bash
cd frontend
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

Current intentional lint warnings:

- model thumbnails use plain `<img>` because authenticated/local API thumbnail
  URLs are not yet routed through Next image optimization.
- `stl-viewer.tsx` keeps the viewer controls effect scoped to the loaded model
  URL.
